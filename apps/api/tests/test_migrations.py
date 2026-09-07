"""Real Alembic migration round-trip tests against a live PostgreSQL
database (BACKEND-03 §10/§17).

These run against ``TEST_MIGRATIONS_DATABASE_URL`` — a database kept
entirely separate from the one `tests/dbtest.py`'s `postgres_engine`
fixture manages directly via `Base.metadata.create_all/drop_all` — so
Alembic's own bookkeeping (the `alembic_version` table, and which
statements it believes have already run) never collides with the
transaction tests' schema management of the same tables.

Skipped, never faked, when no such database is configured/reachable.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.core.config import get_settings
from app.persistence.session import create_db_engine
from app.persistence.testing import UnsafeTestDatabaseError, assert_safe_test_database_url

ALEMBIC_INI = "alembic.ini"
pytestmark = pytest.mark.postgres


@pytest.fixture()
def migrations_database_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    url = os.environ.get("TEST_MIGRATIONS_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_MIGRATIONS_DATABASE_URL is not set. Real Alembic migration "
            "acceptance is skipped (not faked) — set it to a real, reachable, "
            "test-scoped PostgreSQL database, separate from TEST_DATABASE_URL, "
            "to run this."
        )
    try:
        assert_safe_test_database_url(url)
    except UnsafeTestDatabaseError as exc:
        pytest.skip(f"Refusing to run migrations against this database: {exc}")

    engine = create_db_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - exact driver error varies
        pytest.skip(f"TEST_MIGRATIONS_DATABASE_URL is set but not reachable: {exc}")
    finally:
        engine.dispose()

    # env.py resolves the URL via Settings().DATABASE_URL — point it at
    # the dedicated migrations database for the duration of this test.
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        yield url
    finally:
        get_settings.cache_clear()


def _alembic_config() -> Config:
    return Config(ALEMBIC_INI)


def test_migration_upgrades_to_head_on_a_real_database(migrations_database_url: str) -> None:
    config = _alembic_config()
    command.upgrade(config, "head")

    engine = create_db_engine(migrations_database_url)
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = '_infra_persistence_probe')"
                )
            ).scalar_one()
            current_version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()

    assert exists is True
    assert current_version  # a real revision id string, non-empty


def test_migration_round_trips_downgrade_and_upgrade(migrations_database_url: str) -> None:
    config = _alembic_config()

    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_db_engine(migrations_database_url)
    try:
        with engine.connect() as connection:
            probe_exists = connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = '_infra_persistence_probe')"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    assert probe_exists is False

    command.upgrade(config, "head")

    engine = create_db_engine(migrations_database_url)
    try:
        with engine.connect() as connection:
            probe_exists_again = connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = '_infra_persistence_probe')"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    assert probe_exists_again is True
