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


_PRE_BACKEND_06_REVISION = "0977f6691593"
_BACKEND_06_ENUM_TYPES = ("business_stage", "stage_execution_status", "audit_actor_type", "decision_request_status")
_BACKEND_06_TABLES = ("run_stage_executions", "human_decision_requests", "human_decision_responses", "audit_events")


def _orchestration_tables_exist(engine) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'run_stage_executions')"
            )
        ).scalar_one()


def test_backend_06_migration_round_trips_to_the_previous_revision(migrations_database_url: str) -> None:
    """BACKEND-06 §34: upgrade to head, downgrade to exactly the
    pre-BACKEND-06 revision (0977f6691593, not base), upgrade to head
    again — proving the orchestration-foundation migration (including
    the composite-FK tenancy hardening on the existing campaign_runs/
    campaigns tables) adds/removes cleanly without disturbing any
    BACKEND-04/05 table."""
    config = _alembic_config()
    engine = create_db_engine(migrations_database_url)

    try:
        command.upgrade(config, "head")
        assert _orchestration_tables_exist(engine) is True

        command.downgrade(config, _PRE_BACKEND_06_REVISION)
        assert _orchestration_tables_exist(engine) is False
        with engine.connect() as connection:
            for table in _BACKEND_06_TABLES:
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is False, f"{table} must not survive a BACKEND-06 downgrade"

            # BACKEND-04/05 tables must survive a BACKEND-06-only downgrade untouched.
            for table in ("users", "organizations", "workspaces", "memberships", "auth_sessions", "campaigns", "campaign_briefs", "campaign_runs"):
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is True, f"{table} must survive a BACKEND-06-only downgrade"

            leftover_enums = connection.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname = ANY(:names)"
                ),
                {"names": list(_BACKEND_06_ENUM_TYPES)},
            ).scalars().all()
            assert leftover_enums == [], "downgrade must drop every BACKEND-06-owned enum type"

            # BACKEND-04/05-owned enum types must survive untouched.
            surviving_enums = connection.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname IN "
                    "('campaign_status', 'campaign_run_status', 'membership_role', 'membership_status', 'user_status')"
                )
            ).scalars().all()
            assert set(surviving_enums) == {
                "campaign_status",
                "campaign_run_status",
                "membership_role",
                "membership_status",
                "user_status",
            }, "a BACKEND-06 downgrade must not remove any earlier stage's enum type"

            # The BACKEND-06-specific tenancy hardening must also be
            # cleanly reverted: the original single-column FK restored,
            # the new composite unique constraint gone.
            fk_names = connection.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'campaign_runs'::regclass AND contype = 'f'")
            ).scalars().all()
            assert "fk_campaign_runs_campaign_id_campaigns" in fk_names
            assert "fk_campaign_runs_campaign_workspace" not in fk_names
            unique_names = connection.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'campaigns'::regclass AND contype = 'u'")
            ).scalars().all()
            assert "uq_campaigns_id_workspace_id" not in unique_names

        command.upgrade(config, "head")
        assert _orchestration_tables_exist(engine) is True
        with engine.connect() as connection:
            fk_names = connection.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'campaign_runs'::regclass AND contype = 'f'")
            ).scalars().all()
            assert "fk_campaign_runs_campaign_workspace" in fk_names
            assert "fk_campaign_runs_campaign_id_campaigns" not in fk_names
    finally:
        engine.dispose()


_PRE_BACKEND_07_REVISION = "0bb72567beac"
_BACKEND_07_TABLES = ("research_reports", "research_sources", "audience_profiles", "voc_evidence")


def _research_tables_exist(engine) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'research_reports')")
        ).scalar_one()


def test_backend_07_migration_round_trips_to_the_previous_revision(migrations_database_url: str) -> None:
    """BACKEND-07 §21: upgrade to head, downgrade to exactly the
    pre-BACKEND-07 revision (0bb72567beac, not base), upgrade to head
    again — proving the research/audience migration (including the new
    audit_events FK columns and the campaign_runs candidate key) adds/
    removes cleanly without disturbing any BACKEND-04/05/06 object."""
    config = _alembic_config()
    engine = create_db_engine(migrations_database_url)

    try:
        command.upgrade(config, "head")
        assert _research_tables_exist(engine) is True

        command.downgrade(config, _PRE_BACKEND_07_REVISION)
        assert _research_tables_exist(engine) is False
        with engine.connect() as connection:
            for table in _BACKEND_07_TABLES:
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is False, f"{table} must not survive a BACKEND-07 downgrade"

            # Every BACKEND-04/05/06 table must survive a BACKEND-07-only
            # downgrade untouched.
            for table in (
                "users", "organizations", "workspaces", "memberships", "auth_sessions",
                "campaigns", "campaign_briefs", "campaign_runs",
                "run_stage_executions", "human_decision_requests", "human_decision_responses", "audit_events",
            ):
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is True, f"{table} must survive a BACKEND-07-only downgrade"

            leftover_enum = connection.execute(
                text("SELECT typname FROM pg_type WHERE typname = 'source_type'")
            ).scalars().all()
            assert leftover_enum == [], "downgrade must drop the BACKEND-07-owned source_type enum type"

            # BACKEND-04/05/06-owned enum types must survive untouched.
            surviving_enums = connection.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname IN "
                    "('campaign_status', 'campaign_run_status', 'membership_role', 'membership_status', "
                    "'user_status', 'business_stage', 'stage_execution_status', 'audit_actor_type', "
                    "'decision_request_status')"
                )
            ).scalars().all()
            assert len(surviving_enums) == 9, "a BACKEND-07 downgrade must not remove any earlier stage's enum type"

            # audit_events must be reverted to its exact BACKEND-06 shape.
            audit_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_events'")
            ).scalars().all()
            assert "research_report_id" not in audit_columns
            assert "audience_profile_id" not in audit_columns

            # The BACKEND-07-added campaign_runs candidate key must be gone.
            unique_names = connection.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'campaign_runs'::regclass AND contype = 'u'")
            ).scalars().all()
            assert "uq_campaign_runs_id_workspace_id" not in unique_names

        command.upgrade(config, "head")
        assert _research_tables_exist(engine) is True
        with engine.connect() as connection:
            audit_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_events'")
            ).scalars().all()
            assert "research_report_id" in audit_columns
            assert "audience_profile_id" in audit_columns
    finally:
        engine.dispose()
