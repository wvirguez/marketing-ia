"""Shared fixtures for tests marked ``@pytest.mark.postgres``.

These tests require a real, reachable, test-scoped PostgreSQL database.
Per BACKEND-03 §11/§19, they must be **skipped**, never faked, when one
is not configured or not reachable — SQLite is never substituted, since
this application targets PostgreSQL specifically.

Configure ``TEST_DATABASE_URL`` to run them.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.persistence.base import Base
from app.persistence.session import create_db_engine
from app.persistence.testing import UnsafeTestDatabaseError, assert_safe_test_database_url


def _reachable_test_engine(url: str) -> Engine:
    """Applies the safety guard, then proves the URL is actually
    reachable, raising ``pytest.skip`` (not an error) on any failure —
    an environment blocker is not a test failure."""
    try:
        assert_safe_test_database_url(url)
    except UnsafeTestDatabaseError as exc:
        pytest.skip(f"Refusing to use this database for tests: {exc}")

    engine = create_db_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - exact driver error varies
        engine.dispose()
        pytest.skip(f"TEST_DATABASE_URL is set but not reachable ({exc.__class__.__name__}): {exc}")
    return engine


@pytest.fixture(scope="session")
def postgres_engine() -> Iterator[Engine]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL is not set. PostgreSQL acceptance tests are "
            "skipped (not faked with SQLite) — set TEST_DATABASE_URL to a "
            "real, reachable, test-scoped PostgreSQL database to run them."
        )

    engine = _reachable_test_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def db_session(postgres_engine: Engine) -> Iterator[Session]:
    """One Session per test, bound to an outer transaction that is
    always rolled back at teardown — so a test calling `session.commit()`
    (exactly what the session contract allows application code to do)
    still leaves no trace for the next test. This is the standard
    SQLAlchemy pattern for isolating tests against a real database
    without dropping/recreating tables between every single test.
    """
    connection = postgres_engine.connect()
    outer_transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()
