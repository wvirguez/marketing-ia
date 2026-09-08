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


_PRE_BACKEND_05_REVISION = "ce93e0b40957"


def _campaign_tables_exist(engine) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'campaigns')"
            )
        ).scalar_one()


def test_backend_05_migration_round_trips_to_the_previous_revision(migrations_database_url: str) -> None:
    """BACKEND-05 §22: upgrade to head, downgrade to exactly the
    pre-BACKEND-05 revision (not all the way to base), upgrade to head
    again — proving the campaign-domain migration adds/removes cleanly
    without disturbing BACKEND-04's identity/tenancy tables."""
    config = _alembic_config()
    engine = create_db_engine(migrations_database_url)

    try:
        command.upgrade(config, "head")
        assert _campaign_tables_exist(engine) is True

        command.downgrade(config, _PRE_BACKEND_05_REVISION)
        assert _campaign_tables_exist(engine) is False
        with engine.connect() as connection:
            # BACKEND-04 tables must survive a BACKEND-05 downgrade untouched.
            for table in ("users", "organizations", "workspaces", "memberships", "auth_sessions"):
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is True, f"{table} must survive a BACKEND-05-only downgrade"
            leftover_enums = connection.execute(
                text("SELECT typname FROM pg_type WHERE typname LIKE 'campaign%'")
            ).scalars().all()
            assert leftover_enums == [], "downgrade must drop the campaign_status/campaign_run_status enum types"

        command.upgrade(config, "head")
        assert _campaign_tables_exist(engine) is True
    finally:
        engine.dispose()
