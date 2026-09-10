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


_PRE_BACKEND_08_REVISION = "4b9a72f05a4f"
_BACKEND_08_TABLES = ("strategies", "positionings", "hypotheses", "experiments")


def _strategy_tables_exist(engine) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'strategies')")
        ).scalar_one()


def test_backend_08_migration_round_trips_to_the_previous_revision(migrations_database_url: str) -> None:
    """BACKEND-08 §23: upgrade to head, downgrade to exactly the
    pre-BACKEND-08 revision (4b9a72f05a4f, not base), upgrade to head
    again — proving the strategy migration (including the new
    audit_events FK columns and the two new candidate keys on strategies/
    hypotheses) adds/removes cleanly without disturbing any BACKEND-04/05/
    06/07 object."""
    config = _alembic_config()
    engine = create_db_engine(migrations_database_url)

    try:
        command.upgrade(config, "head")
        assert _strategy_tables_exist(engine) is True

        command.downgrade(config, _PRE_BACKEND_08_REVISION)
        assert _strategy_tables_exist(engine) is False
        with engine.connect() as connection:
            for table in _BACKEND_08_TABLES:
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is False, f"{table} must not survive a BACKEND-08 downgrade"

            # Every BACKEND-04/05/06/07 table must survive a BACKEND-08-only
            # downgrade untouched.
            for table in (
                "users", "organizations", "workspaces", "memberships", "auth_sessions",
                "campaigns", "campaign_briefs", "campaign_runs",
                "run_stage_executions", "human_decision_requests", "human_decision_responses", "audit_events",
                "research_reports", "research_sources", "audience_profiles", "voc_evidence",
            ):
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is True, f"{table} must survive a BACKEND-08-only downgrade"

            leftover_enum = connection.execute(
                text("SELECT typname FROM pg_type WHERE typname = 'hypothesis_status'")
            ).scalars().all()
            assert leftover_enum == [], "downgrade must drop the BACKEND-08-owned hypothesis_status enum type"

            # Every earlier stage's enum type must survive untouched.
            surviving_enums = connection.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname IN "
                    "('campaign_status', 'campaign_run_status', 'membership_role', 'membership_status', "
                    "'user_status', 'business_stage', 'stage_execution_status', 'audit_actor_type', "
                    "'decision_request_status', 'source_type')"
                )
            ).scalars().all()
            assert len(surviving_enums) == 10, "a BACKEND-08 downgrade must not remove any earlier stage's enum type"

            # audit_events must be reverted to its exact pre-BACKEND-08 shape.
            audit_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_events'")
            ).scalars().all()
            assert "strategy_id" not in audit_columns
            assert "hypothesis_id" not in audit_columns
            assert "experiment_id" not in audit_columns

            # The BACKEND-08-added candidate keys must be gone.
            strategies_still_exists = connection.execute(
                text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'strategies')")
            ).scalar_one()
            assert strategies_still_exists is False

        command.upgrade(config, "head")
        assert _strategy_tables_exist(engine) is True
        with engine.connect() as connection:
            audit_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_events'")
            ).scalars().all()
            assert "strategy_id" in audit_columns
            assert "hypothesis_id" in audit_columns
            assert "experiment_id" in audit_columns

            unique_names_strategies = connection.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'strategies'::regclass AND contype = 'u'")
            ).scalars().all()
            assert "uq_strategies_id_workspace_id" in unique_names_strategies

            unique_names_hypotheses = connection.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'hypotheses'::regclass AND contype = 'u'")
            ).scalars().all()
            assert "uq_hypotheses_id_workspace_id" in unique_names_hypotheses
    finally:
        engine.dispose()


_PRE_BACKEND_09_REVISION = "5e50463be153"
_BACKEND_09_TABLES = ("content_plans", "plan_items")


def _planning_tables_exist(engine) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'content_plans')")
        ).scalar_one()


def test_backend_09_migration_round_trips_to_the_previous_revision(migrations_database_url: str) -> None:
    """BACKEND-09 §22: upgrade to head, downgrade to exactly the
    pre-BACKEND-09 revision (5e50463be153, not base), upgrade to head
    again — proving the planning migration (content_plans/plan_items plus
    the new audit_events FK columns) adds/removes cleanly without
    disturbing any BACKEND-04/05/06/07/08 object, and introduces no
    unnecessary native enum type."""
    config = _alembic_config()
    engine = create_db_engine(migrations_database_url)

    try:
        command.upgrade(config, "head")
        assert _planning_tables_exist(engine) is True

        command.downgrade(config, _PRE_BACKEND_09_REVISION)
        assert _planning_tables_exist(engine) is False
        with engine.connect() as connection:
            for table in _BACKEND_09_TABLES:
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is False, f"{table} must not survive a BACKEND-09 downgrade"

            # Every BACKEND-04/05/06/07/08 table must survive a
            # BACKEND-09-only downgrade untouched.
            for table in (
                "users", "organizations", "workspaces", "memberships", "auth_sessions",
                "campaigns", "campaign_briefs", "campaign_runs",
                "run_stage_executions", "human_decision_requests", "human_decision_responses", "audit_events",
                "research_reports", "research_sources", "audience_profiles", "voc_evidence",
                "strategies", "positionings", "hypotheses", "experiments",
            ):
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is True, f"{table} must survive a BACKEND-09-only downgrade"

            # Every earlier stage's enum type must survive untouched, and
            # BACKEND-09 must not have introduced a new one at all (no
            # canonical vocabulary exists for `format`/`objective`).
            surviving_enums = connection.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname IN "
                    "('campaign_status', 'campaign_run_status', 'membership_role', 'membership_status', "
                    "'user_status', 'business_stage', 'stage_execution_status', 'audit_actor_type', "
                    "'decision_request_status', 'source_type', 'hypothesis_status')"
                )
            ).scalars().all()
            assert len(surviving_enums) == 11, "a BACKEND-09 downgrade must not remove any earlier stage's enum type"

            # audit_events must be reverted to its exact pre-BACKEND-09 shape.
            audit_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_events'")
            ).scalars().all()
            assert "content_plan_id" not in audit_columns
            assert "plan_item_id" not in audit_columns

        command.upgrade(config, "head")
        assert _planning_tables_exist(engine) is True
        with engine.connect() as connection:
            audit_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_events'")
            ).scalars().all()
            assert "content_plan_id" in audit_columns
            assert "plan_item_id" in audit_columns

            # The corrected FK contract: plan_item_id -> plan_items.id,
            # never content_plans.id.
            fk_rows = connection.execute(
                text(
                    "SELECT conname, confrelid::regclass::text FROM pg_constraint "
                    "WHERE conrelid = 'audit_events'::regclass AND contype = 'f' "
                    "AND conname LIKE '%plan_item_id%'"
                )
            ).all()
            assert len(fk_rows) == 1
            assert fk_rows[0][1] == "plan_items"
    finally:
        engine.dispose()


_PRE_BACKEND_10_REVISION = "5a6a04e3776d"
_BACKEND_10_TABLES = ("content_briefs", "content_pieces", "content_versions", "content_approvals")


def _content_tables_exist(engine) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'content_briefs')")
        ).scalar_one()


def test_backend_10_migration_round_trips_to_the_previous_revision(migrations_database_url: str) -> None:
    """BACKEND-10 §52: upgrade to head, downgrade to exactly the
    pre-BACKEND-10 revision (5a6a04e3776d, not base), upgrade to head
    again — proving the content migration (content_briefs/content_pieces/
    content_versions/content_approvals, the two new native enums, the new
    audit_events FK columns, and the additive ContentPlan candidate key)
    adds/removes cleanly without disturbing any BACKEND-04..09 object."""
    config = _alembic_config()
    engine = create_db_engine(migrations_database_url)

    try:
        command.upgrade(config, "head")
        assert _content_tables_exist(engine) is True

        command.downgrade(config, _PRE_BACKEND_10_REVISION)
        assert _content_tables_exist(engine) is False
        with engine.connect() as connection:
            for table in _BACKEND_10_TABLES:
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is False, f"{table} must not survive a BACKEND-10 downgrade"

            # Every BACKEND-04..09 table must survive a BACKEND-10-only
            # downgrade untouched.
            for table in (
                "users", "organizations", "workspaces", "memberships", "auth_sessions",
                "campaigns", "campaign_briefs", "campaign_runs",
                "run_stage_executions", "human_decision_requests", "human_decision_responses", "audit_events",
                "research_reports", "research_sources", "audience_profiles", "voc_evidence",
                "strategies", "positionings", "hypotheses", "experiments",
                "content_plans", "plan_items",
            ):
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is True, f"{table} must survive a BACKEND-10-only downgrade"

            # Every earlier stage's enum type must survive untouched, and
            # both BACKEND-10-owned enums must be gone.
            surviving_enums = connection.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname IN "
                    "('campaign_status', 'campaign_run_status', 'membership_role', 'membership_status', "
                    "'user_status', 'business_stage', 'stage_execution_status', 'audit_actor_type', "
                    "'decision_request_status', 'source_type', 'hypothesis_status')"
                )
            ).scalars().all()
            assert len(surviving_enums) == 11, "a BACKEND-10 downgrade must not remove any earlier stage's enum type"

            leftover_enums = connection.execute(
                text("SELECT typname FROM pg_type WHERE typname IN ('content_piece_status', 'content_approval_status')")
            ).scalars().all()
            assert leftover_enums == [], "downgrade must drop both BACKEND-10-owned enum types"

            # audit_events must be reverted to its exact pre-BACKEND-10 shape.
            audit_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_events'")
            ).scalars().all()
            for column in ("content_brief_id", "content_piece_id", "content_version_id", "content_approval_id"):
                assert column not in audit_columns

            # The BACKEND-10-added ContentPlan candidate key must be gone.
            unique_names = connection.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'content_plans'::regclass AND contype = 'u'")
            ).scalars().all()
            assert "uq_content_plans_id_workspace_id" not in unique_names

        command.upgrade(config, "head")
        assert _content_tables_exist(engine) is True
        with engine.connect() as connection:
            audit_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_events'")
            ).scalars().all()
            for column in ("content_brief_id", "content_piece_id", "content_version_id", "content_approval_id"):
                assert column in audit_columns

            unique_names = connection.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'content_plans'::regclass AND contype = 'u'")
            ).scalars().all()
            assert "uq_content_plans_id_workspace_id" in unique_names

            # The tenant-safety FK contract: content_briefs.content_plan_id
            # (composite, through content_plans), never a direct
            # campaign_id/campaign_run_id/stage_execution_id column.
            brief_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'content_briefs'")
            ).scalars().all()
            for forbidden in ("campaign_id", "campaign_run_id", "stage_execution_id", "workspace_id_2"):
                assert forbidden not in brief_columns
            assert "workspace_id" in brief_columns
            assert "content_plan_id" in brief_columns
    finally:
        engine.dispose()


_PRE_BACKEND_11_REVISION = "acaad5704928"
_BACKEND_11_TABLES = (
    "metric_entries", "metric_values", "performance_observations", "performance_signals", "analysis_results",
    "observation_metric_entries", "signal_observations", "analysis_result_signals",
)


def _measurement_tables_exist(engine) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'metric_entries')")
        ).scalar_one()


def test_backend_11_migration_round_trips_to_the_previous_revision(migrations_database_url: str) -> None:
    """BACKEND-11 §18: upgrade to head, downgrade to exactly the
    pre-BACKEND-11 revision (acaad5704928, not base), upgrade to head
    again — proving the measurement migration (four core tables, three
    pure association tables, the new native metric_source enum, and the
    new audit_events FK columns) adds/removes cleanly without disturbing
    any BACKEND-04..10 object."""
    config = _alembic_config()
    engine = create_db_engine(migrations_database_url)

    try:
        command.upgrade(config, "head")
        assert _measurement_tables_exist(engine) is True

        command.downgrade(config, _PRE_BACKEND_11_REVISION)
        assert _measurement_tables_exist(engine) is False
        with engine.connect() as connection:
            for table in _BACKEND_11_TABLES:
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is False, f"{table} must not survive a BACKEND-11 downgrade"

            # Every BACKEND-04..10 table must survive a BACKEND-11-only
            # downgrade untouched.
            for table in (
                "users", "organizations", "workspaces", "memberships", "auth_sessions",
                "campaigns", "campaign_briefs", "campaign_runs",
                "run_stage_executions", "human_decision_requests", "human_decision_responses", "audit_events",
                "research_reports", "research_sources", "audience_profiles", "voc_evidence",
                "strategies", "positionings", "hypotheses", "experiments",
                "content_plans", "plan_items",
                "content_briefs", "content_pieces", "content_versions", "content_approvals",
            ):
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is True, f"{table} must survive a BACKEND-11-only downgrade"

            # Every earlier stage's enum type must survive untouched, and
            # the BACKEND-11-owned metric_source enum must be gone.
            surviving_enums = connection.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname IN "
                    "('campaign_status', 'campaign_run_status', 'membership_role', 'membership_status', "
                    "'user_status', 'business_stage', 'stage_execution_status', 'audit_actor_type', "
                    "'decision_request_status', 'source_type', 'hypothesis_status', "
                    "'content_piece_status', 'content_approval_status')"
                )
            ).scalars().all()
            assert len(surviving_enums) == 13, "a BACKEND-11 downgrade must not remove any earlier stage's enum type"

            leftover_enum = connection.execute(
                text("SELECT typname FROM pg_type WHERE typname = 'metric_source'")
            ).scalars().all()
            assert leftover_enum == [], "downgrade must drop the BACKEND-11-owned metric_source enum type"

            # audit_events must be reverted to its exact pre-BACKEND-11 shape.
            audit_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_events'")
            ).scalars().all()
            for column in ("metric_entry_id", "performance_observation_id", "performance_signal_id", "analysis_result_id"):
                assert column not in audit_columns

        command.upgrade(config, "head")
        assert _measurement_tables_exist(engine) is True
        with engine.connect() as connection:
            audit_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_events'")
            ).scalars().all()
            for column in ("metric_entry_id", "performance_observation_id", "performance_signal_id", "analysis_result_id"):
                assert column in audit_columns

            # No natural-key unique constraint on metric_entries — only the
            # idempotency key.
            unique_constraints = connection.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid = 'metric_entries'::regclass AND contype = 'u'"
                )
            ).scalars().all()
            assert "uq_metric_entries_workspace_client_request_id" in unique_constraints
            assert "uq_metric_entries_id_workspace_id" in unique_constraints
    finally:
        engine.dispose()


_PRE_BACKEND_12_REVISION = "654d662d78e7"
_BACKEND_12_TABLES = ("user_preferences", "ai_preferences", "notification_preferences")


def _settings_tables_exist(engine) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'user_preferences')")
        ).scalar_one()


def _audit_events_workspace_id_is_nullable(engine) -> bool:
    with engine.connect() as connection:
        is_nullable = connection.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'audit_events' AND column_name = 'workspace_id'"
            )
        ).scalar_one()
        return is_nullable == "YES"


def test_backend_12_migration_round_trips_to_the_previous_revision(migrations_database_url: str) -> None:
    """BACKEND-12: upgrade to head, downgrade to exactly the
    pre-BACKEND-12 revision (654d662d78e7, not base), upgrade to head
    again — proving the Settings migration (three new singleton tables
    plus the ``audit_events.workspace_id`` nullability repair) adds/
    removes cleanly without disturbing any BACKEND-04..11 object.

    Known, documented, non-destructive limitation (Phase 2 §27): this
    round-trip never inserts a user-global (``workspace_id IS NULL``)
    AuditEvent row, so restoring ``NOT NULL`` on downgrade always
    succeeds here. A real database that had already recorded a
    ``user.profile.updated``/``user.preferences.updated`` event could not
    cleanly downgrade past this revision without first removing those
    rows — this migration deliberately does not add destructive cleanup
    to paper over that, per explicit instruction."""
    config = _alembic_config()
    engine = create_db_engine(migrations_database_url)

    try:
        command.upgrade(config, "head")
        assert _settings_tables_exist(engine) is True
        assert _audit_events_workspace_id_is_nullable(engine) is True

        command.downgrade(config, _PRE_BACKEND_12_REVISION)
        assert _settings_tables_exist(engine) is False
        assert _audit_events_workspace_id_is_nullable(engine) is False
        with engine.connect() as connection:
            for table in _BACKEND_12_TABLES:
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is False, f"{table} must not survive a BACKEND-12 downgrade"

            # Every BACKEND-04..11 table, including Measurement's own,
            # must survive a BACKEND-12-only downgrade untouched.
            for table in (
                "users", "organizations", "workspaces", "memberships", "auth_sessions",
                "campaigns", "campaign_briefs", "campaign_runs",
                "run_stage_executions", "human_decision_requests", "human_decision_responses", "audit_events",
                "research_reports", "research_sources", "audience_profiles", "voc_evidence",
                "strategies", "positionings", "hypotheses", "experiments",
                "content_plans", "plan_items",
                "content_briefs", "content_pieces", "content_versions", "content_approvals",
                *_BACKEND_11_TABLES,
            ):
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is True, f"{table} must survive a BACKEND-12-only downgrade"

            # Every earlier stage's enum type must survive untouched — this
            # migration introduces no new enum type at all (no native enum
            # for tone/depth/creativity, no notification-type enum).
            surviving_enums = connection.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname IN "
                    "('campaign_status', 'campaign_run_status', 'membership_role', 'membership_status', "
                    "'user_status', 'business_stage', 'stage_execution_status', 'audit_actor_type', "
                    "'decision_request_status', 'source_type', 'hypothesis_status', "
                    "'content_piece_status', 'content_approval_status', 'metric_source')"
                )
            ).scalars().all()
            assert len(surviving_enums) == 14, "a BACKEND-12 downgrade must not remove any earlier stage's enum type"

        command.upgrade(config, "head")
        assert _settings_tables_exist(engine) is True
        assert _audit_events_workspace_id_is_nullable(engine) is True
        with engine.connect() as connection:
            # user_preferences.user_id and ai_preferences.workspace_id are
            # each declared `unique=True, index=True` on the model — a
            # unique INDEX, not a separate named UniqueConstraint (unlike
            # notification_preferences' composite key below, which is an
            # explicit UniqueConstraint in __table_args__).
            def _is_unique_index(table: str, index: str) -> bool:
                row = connection.execute(
                    text("SELECT indexdef FROM pg_indexes WHERE tablename = :t AND indexname = :i"),
                    {"t": table, "i": index},
                ).scalar_one_or_none()
                return row is not None and "UNIQUE" in row

            assert _is_unique_index("user_preferences", "ix_user_preferences_user_id")
            assert _is_unique_index("ai_preferences", "ix_ai_preferences_workspace_id")
            notification_constraints = connection.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid = 'notification_preferences'::regclass "
                    "AND contype = 'u'"
                )
            ).scalars().all()
            assert "uq_notification_preferences_workspace_id_user_id" in notification_constraints
    finally:
        engine.dispose()


_PRE_BACKEND_13_REVISION = "e1df89898ff2"
_BACKEND_13_TABLES = ("creative_briefs", "assets", "asset_versions")


def _assets_tables_exist(engine) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'creative_briefs')")
        ).scalar_one()


def _content_pieces_candidate_key_exists(engine) -> bool:
    with engine.connect() as connection:
        names = connection.execute(
            text("SELECT conname FROM pg_constraint WHERE conrelid = 'content_pieces'::regclass AND contype = 'u'")
        ).scalars().all()
        return "uq_content_pieces_id_workspace_id" in names


def test_backend_13_migration_round_trips_to_the_previous_revision(migrations_database_url: str) -> None:
    """BACKEND-13 Phase 2 §28/§33: upgrade to head, downgrade to exactly
    the pre-BACKEND-13 revision (e1df89898ff2, not base), upgrade to head
    again — proving the Assets migration (the authorized content_pieces
    candidate-key prerequisite repair, three new tables, and the new
    audit_events FK columns) adds/removes cleanly, in the required
    dependency order, without disturbing any BACKEND-04..12 object."""
    config = _alembic_config()
    engine = create_db_engine(migrations_database_url)

    try:
        command.upgrade(config, "head")
        assert _assets_tables_exist(engine) is True
        assert _content_pieces_candidate_key_exists(engine) is True

        command.downgrade(config, _PRE_BACKEND_13_REVISION)
        assert _assets_tables_exist(engine) is False
        assert _content_pieces_candidate_key_exists(engine) is False
        with engine.connect() as connection:
            for table in _BACKEND_13_TABLES:
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is False, f"{table} must not survive a BACKEND-13 downgrade"

            # Every BACKEND-04..12 table must survive a BACKEND-13-only
            # downgrade untouched.
            for table in (
                "users", "user_preferences", "organizations", "workspaces", "memberships", "auth_sessions",
                "ai_preferences", "notification_preferences",
                "campaigns", "campaign_briefs", "campaign_runs",
                "run_stage_executions", "human_decision_requests", "human_decision_responses", "audit_events",
                "research_reports", "research_sources", "audience_profiles", "voc_evidence",
                "strategies", "positionings", "hypotheses", "experiments",
                "content_plans", "plan_items",
                "content_briefs", "content_pieces", "content_versions", "content_approvals",
                *_BACKEND_11_TABLES,
            ):
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is True, f"{table} must survive a BACKEND-13-only downgrade"

            # audit_events must be reverted to its exact pre-BACKEND-13 shape.
            audit_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_events'")
            ).scalars().all()
            for column in ("creative_brief_id", "asset_id", "asset_version_id"):
                assert column not in audit_columns

            # No native enum type is introduced by BACKEND-13 at all (no
            # canonical kind/status vocabulary is named anywhere).
            surviving_enums = connection.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname IN "
                    "('campaign_status', 'campaign_run_status', 'membership_role', 'membership_status', "
                    "'user_status', 'business_stage', 'stage_execution_status', 'audit_actor_type', "
                    "'decision_request_status', 'source_type', 'hypothesis_status', "
                    "'content_piece_status', 'content_approval_status', 'metric_source')"
                )
            ).scalars().all()
            assert len(surviving_enums) == 14, "a BACKEND-13 downgrade must not remove any earlier stage's enum type"

        command.upgrade(config, "head")
        assert _assets_tables_exist(engine) is True
        assert _content_pieces_candidate_key_exists(engine) is True
        with engine.connect() as connection:
            audit_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_events'")
            ).scalars().all()
            for column in ("creative_brief_id", "asset_id", "asset_version_id"):
                assert column in audit_columns

            # The tenant-safety FK contract: creative_briefs.content_piece_id
            # (composite, through content_pieces), never a direct
            # campaign_id/campaign_run_id/stage_execution_id column.
            brief_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'creative_briefs'")
            ).scalars().all()
            for forbidden in ("campaign_id", "campaign_run_id", "stage_execution_id", "public_id"):
                assert forbidden not in brief_columns
            assert "workspace_id" in brief_columns
            assert "content_piece_id" in brief_columns

            unique_names = connection.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'creative_briefs'::regclass AND contype = 'u'")
            ).scalars().all()
            assert "uq_creative_briefs_content_piece_id" in unique_names
            assert "uq_creative_briefs_id_workspace_id" in unique_names

            # Phase 2R §7: no UNIQUE(id, workspace_id) on assets — no FK
            # anywhere actually targets that composite (AssetVersion has
            # no workspace_id at all; AuditEvent.asset_id is a plain
            # single-column FK), so the candidate key was removed as
            # speculative rather than kept "just in case".
            asset_unique_names = connection.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'assets'::regclass AND contype = 'u'")
            ).scalars().all()
            assert "uq_assets_id_workspace_id" not in asset_unique_names
    finally:
        engine.dispose()


_PRE_BACKEND_14_REVISION = "8e43d97d836c"
_BACKEND_14_TABLES = ("learning_candidates", "strategic_recommendation_candidates")


def _learning_tables_exist(engine) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'learning_candidates')")
        ).scalar_one()


def test_backend_14_migration_round_trips_to_the_previous_revision(migrations_database_url: str) -> None:
    """BACKEND-14 Phase 2 §22: upgrade to head, downgrade to exactly the
    pre-BACKEND-14 revision (8e43d97d836c, not base), upgrade to head
    again — proving the Learning migration (two new tables, two new
    native enums, and the new audit_events FK columns) adds/removes
    cleanly without disturbing any BACKEND-04..13 object, and requires no
    closed-domain schema change (analysis_results already carried its own
    candidate key)."""
    config = _alembic_config()
    engine = create_db_engine(migrations_database_url)

    try:
        command.upgrade(config, "head")
        assert _learning_tables_exist(engine) is True

        command.downgrade(config, _PRE_BACKEND_14_REVISION)
        assert _learning_tables_exist(engine) is False
        with engine.connect() as connection:
            for table in _BACKEND_14_TABLES:
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is False, f"{table} must not survive a BACKEND-14 downgrade"

            # Every BACKEND-04..13 table must survive a BACKEND-14-only
            # downgrade untouched.
            for table in (
                "users", "user_preferences", "organizations", "workspaces", "memberships", "auth_sessions",
                "ai_preferences", "notification_preferences",
                "campaigns", "campaign_briefs", "campaign_runs",
                "run_stage_executions", "human_decision_requests", "human_decision_responses", "audit_events",
                "research_reports", "research_sources", "audience_profiles", "voc_evidence",
                "strategies", "positionings", "hypotheses", "experiments",
                "content_plans", "plan_items",
                "content_briefs", "content_pieces", "content_versions", "content_approvals",
                *_BACKEND_11_TABLES,
                "creative_briefs", "assets", "asset_versions",
            ):
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is True, f"{table} must survive a BACKEND-14-only downgrade"

            # audit_events must be reverted to its exact pre-BACKEND-14 shape.
            audit_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_events'")
            ).scalars().all()
            for column in ("learning_candidate_id", "strategic_recommendation_candidate_id"):
                assert column not in audit_columns

            # Both BACKEND-14-owned enum types must be gone; every earlier
            # stage's enum type must survive untouched.
            leftover_enums = connection.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname IN "
                    "('learning_candidate_status', 'strategic_recommendation_decision')"
                )
            ).scalars().all()
            assert leftover_enums == [], "downgrade must drop both BACKEND-14-owned enum types"

            surviving_enums = connection.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname IN "
                    "('campaign_status', 'campaign_run_status', 'membership_role', 'membership_status', "
                    "'user_status', 'business_stage', 'stage_execution_status', 'audit_actor_type', "
                    "'decision_request_status', 'source_type', 'hypothesis_status', "
                    "'content_piece_status', 'content_approval_status', 'metric_source')"
                )
            ).scalars().all()
            assert len(surviving_enums) == 14, "a BACKEND-14 downgrade must not remove any earlier stage's enum type"

            # No closed-domain repair was needed for BACKEND-14 — confirm
            # analysis_results' own candidate key, already added by
            # BACKEND-11, is untouched.
            analysis_result_unique_names = connection.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'analysis_results'::regclass AND contype = 'u'")
            ).scalars().all()
            assert "uq_analysis_results_id_workspace_id" in analysis_result_unique_names

        command.upgrade(config, "head")
        assert _learning_tables_exist(engine) is True
        with engine.connect() as connection:
            audit_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_events'")
            ).scalars().all()
            for column in ("learning_candidate_id", "strategic_recommendation_candidate_id"):
                assert column in audit_columns

            # The tenant-safety FK contract: learning_candidates.analysis_result_id
            # (composite, through analysis_results), never a direct
            # campaign_id/campaign_run_id/experiment_id column.
            candidate_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'learning_candidates'")
            ).scalars().all()
            for forbidden in ("campaign_id", "campaign_run_id", "experiment_id", "content_piece_id", "public_id_2"):
                assert forbidden not in candidate_columns
            assert "workspace_id" in candidate_columns
            assert "analysis_result_id" in candidate_columns

            unique_names = connection.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'learning_candidates'::regclass AND contype = 'u'")
            ).scalars().all()
            assert "uq_learning_candidates_id_workspace_id" in unique_names

            # No speculative candidate key on strategic_recommendation_candidates.
            src_unique_names = connection.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid = "
                    "'strategic_recommendation_candidates'::regclass AND contype = 'u'"
                )
            ).scalars().all()
            assert "uq_strategic_recommendation_candidates_id_workspace_id" not in src_unique_names
    finally:
        engine.dispose()


_PRE_BACKEND_15_REVISION = "448a7fa7936d"
_BACKEND_15_TABLES = ("tracking_plans", "tracking_requirements")


def _tracking_tables_exist(engine) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'tracking_plans')")
        ).scalar_one()


def test_backend_15_migration_round_trips_to_the_previous_revision(migrations_database_url: str) -> None:
    """BACKEND-15 Phase 2: upgrade to head, downgrade to exactly the
    pre-BACKEND-15 revision (448a7fa7936d, not base), upgrade to head
    again — proving the Tracking migration (two new tables, one new
    native enum, and the new audit_events FK columns) adds/removes
    cleanly without disturbing any BACKEND-04..14 object, and requires no
    closed-domain schema change (campaigns already carried its own
    candidate key)."""
    config = _alembic_config()
    engine = create_db_engine(migrations_database_url)

    try:
        command.upgrade(config, "head")
        assert _tracking_tables_exist(engine) is True

        command.downgrade(config, _PRE_BACKEND_15_REVISION)
        assert _tracking_tables_exist(engine) is False
        with engine.connect() as connection:
            for table in _BACKEND_15_TABLES:
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is False, f"{table} must not survive a BACKEND-15 downgrade"

            # Every BACKEND-04..14 table must survive a BACKEND-15-only
            # downgrade untouched.
            for table in (
                "users", "user_preferences", "organizations", "workspaces", "memberships", "auth_sessions",
                "ai_preferences", "notification_preferences",
                "campaigns", "campaign_briefs", "campaign_runs",
                "run_stage_executions", "human_decision_requests", "human_decision_responses", "audit_events",
                "research_reports", "research_sources", "audience_profiles", "voc_evidence",
                "strategies", "positionings", "hypotheses", "experiments",
                "content_plans", "plan_items",
                "content_briefs", "content_pieces", "content_versions", "content_approvals",
                *_BACKEND_11_TABLES,
                "creative_briefs", "assets", "asset_versions",
                *_BACKEND_14_TABLES,
            ):
                exists = connection.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
                ).scalar_one()
                assert exists is True, f"{table} must survive a BACKEND-15-only downgrade"

            # audit_events must be reverted to its exact pre-BACKEND-15 shape.
            audit_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_events'")
            ).scalars().all()
            for column in ("tracking_plan_id", "tracking_requirement_id"):
                assert column not in audit_columns

            # The BACKEND-15-owned enum type must be gone; every earlier
            # stage's enum type must survive untouched.
            leftover_enums = connection.execute(
                text("SELECT typname FROM pg_type WHERE typname = 'tracking_readiness_status'")
            ).scalars().all()
            assert leftover_enums == [], "downgrade must drop the BACKEND-15-owned enum type"

            surviving_enums = connection.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname IN "
                    "('campaign_status', 'campaign_run_status', 'membership_role', 'membership_status', "
                    "'user_status', 'business_stage', 'stage_execution_status', 'audit_actor_type', "
                    "'decision_request_status', 'source_type', 'hypothesis_status', "
                    "'content_piece_status', 'content_approval_status', 'metric_source', "
                    "'learning_candidate_status', 'strategic_recommendation_decision')"
                )
            ).scalars().all()
            assert len(surviving_enums) == 16, "a BACKEND-15 downgrade must not remove any earlier stage's enum type"

            # No closed-domain repair was needed for BACKEND-15 — confirm
            # campaigns' own candidate key, already added by BACKEND-05,
            # is untouched.
            campaign_unique_names = connection.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'campaigns'::regclass AND contype = 'u'")
            ).scalars().all()
            assert "uq_campaigns_id_workspace_id" in campaign_unique_names

        command.upgrade(config, "head")
        assert _tracking_tables_exist(engine) is True
        with engine.connect() as connection:
            audit_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_events'")
            ).scalars().all()
            for column in ("tracking_plan_id", "tracking_requirement_id"):
                assert column in audit_columns

            # The tenant-safety FK contract: tracking_plans.campaign_id
            # (composite, through campaigns), never a direct
            # workspace-only shortcut; tracking_requirements has no
            # workspace_id/campaign_id of its own (TRK-D19).
            plan_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'tracking_plans'")
            ).scalars().all()
            assert "workspace_id" in plan_columns
            assert "campaign_id" in plan_columns

            requirement_columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'tracking_requirements'")
            ).scalars().all()
            for forbidden in ("workspace_id", "campaign_id"):
                assert forbidden not in requirement_columns

            unique_names = connection.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'tracking_plans'::regclass AND contype = 'u'")
            ).scalars().all()
            assert "uq_tracking_plans_campaign_workspace" in unique_names
            assert "uq_tracking_plans_id_workspace_id" not in unique_names
    finally:
        engine.dispose()
