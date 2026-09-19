"""MVP-37 reproducible round trip for the Experiment Definition migration
(``7c2e91b4d0a8``, down_revision ``41687fa37c1c``) against the dedicated,
disposable migration database, plus model <-> DB parity against the
``create_all`` application-test database — mirrors
``tests/test_commercial_outcome_migration.py``, one migration later."""
import logging
import os

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from app.core.config import get_settings
from app.persistence.testing import assert_safe_test_database_url

pytestmark = pytest.mark.postgres

REVISION = "7c2e91b4d0a8"
PREDECESSOR = "41687fa37c1c"
TABLE = "experiment_definition_versions"

_CONSTRAINT_DEFS = """
    select conname, contype, pg_get_constraintdef(oid) as definition
    from pg_constraint
    where conrelid = to_regclass(:table)
    order by conname
"""


@pytest.fixture(autouse=True)
def _restore_logging_state_after_alembic():
    """See ``tests/test_commercial_migration.py``: ``alembic/env.py`` disables
    every already-registered logger on each upgrade/downgrade; restore the
    snapshot so this file's Alembic calls do not leak that side effect."""
    snapshot = {
        name: logger.disabled
        for name, logger in logging.Logger.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }
    try:
        yield
    finally:
        for name, disabled in snapshot.items():
            logger = logging.Logger.manager.loggerDict.get(name)
            if isinstance(logger, logging.Logger):
                logger.disabled = disabled


def test_migration_is_a_single_step_after_its_predecessor_and_is_current_head():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_revision(REVISION).down_revision == PREDECESSOR
    assert script.get_heads() == [REVISION]


def _constraint_definitions(engine) -> dict[str, tuple[str, str]]:
    with engine.connect() as connection:
        return {
            row.conname: (row.contype, row.definition)
            for row in connection.execute(text(_CONSTRAINT_DEFS), {"table": TABLE})
        }


def test_experiment_definition_migration_round_trip(monkeypatch, postgres_engine):
    url = os.environ.get("TEST_MIGRATIONS_DATABASE_URL")
    if not url:
        pytest.skip("TEST_MIGRATIONS_DATABASE_URL requires a separate disposable PostgreSQL database")
    assert_safe_test_database_url(url)
    assert url != os.environ.get("TEST_DATABASE_URL"), "Migration DB must be isolated from application tests"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    engine = create_engine(url)
    config = Config("alembic.ini")
    try:
        # Re-runnable only in the explicitly test-scoped database above.
        if TABLE in inspect(engine).get_table_names():
            command.downgrade(config, PREDECESSOR)
        command.upgrade(config, PREDECESSOR)
        inspector = inspect(engine)
        assert TABLE not in inspector.get_table_names()
        assert "experiment_definition_version_id" not in {c["name"] for c in inspector.get_columns("audit_events")}
        with engine.connect() as connection:
            assert connection.scalar(text("select version_num from alembic_version")) == PREDECESSOR

        for cycle in range(2):
            command.upgrade(config, "head")
            inspector = inspect(engine)

            from app.strategy.models import ExperimentDefinitionVersion

            table = ExperimentDefinitionVersion.__table__
            columns = inspector.get_columns(TABLE)
            assert {c["name"] for c in columns} == set(table.columns.keys())
            assert {c["name"]: c["nullable"] for c in columns} == {c.name: c.nullable for c in table.columns}
            # Frozen MVP-37B: none of these may ever exist on the version table.
            assert not {
                "updated_at", "created_by", "campaign_id", "status", "is_frozen", "is_locked", "variant_id",
                "required_signal", "success_criterion", "allocation",
            } & {c["name"] for c in columns}

            fks = inspector.get_foreign_keys(TABLE)
            assert {tuple(fk["constrained_columns"]) for fk in fks} == {
                tuple(c.name for c in fk.columns) for fk in table.foreign_key_constraints
            }
            assert "fk_experiment_definition_versions_experiment_workspace" in {fk["name"] for fk in fks}
            assert all(fk["options"].get("ondelete") != "CASCADE" for fk in fks)
            assert all(len(fk["name"]) <= 63 for fk in fks)
            assert all(len(i["name"]) <= 63 for i in inspector.get_indexes(TABLE))

            uniques = {u["name"] for u in inspector.get_unique_constraints(TABLE)}
            assert {
                "uq_experiment_definition_versions_experiment_version",
                "uq_experiment_definition_versions_workspace_client_request_id",
            } <= uniques
            index_names = {i["name"] for i in inspector.get_indexes(TABLE)}
            assert {
                "ix_experiment_definition_versions_public_id",
                "ix_experiment_definition_versions_workspace_id",
                "ix_experiment_definition_versions_experiment_id",
            } <= index_names
            checks = {c["name"] for c in inspector.get_check_constraints(TABLE)}
            assert checks == {
                "ck_experiment_definition_versions_comparison_type_valid",
                "ck_experiment_definition_versions_version_positive",
                "ck_experiment_definition_versions_controlled_factors_is_array",
                "ck_experiment_definition_versions_controlled_factors_max_count",
                "ck_experiment_definition_versions_controlled_needs_factors",
                "ck_experiment_definition_versions_text_fields_nonblank",
            }

            audit_columns = {c["name"]: c["nullable"] for c in inspector.get_columns("audit_events")}
            assert audit_columns["experiment_definition_version_id"] is True
            audit_fks = inspector.get_foreign_keys("audit_events")
            assert any(
                fk["name"] == "fk_audit_events_experiment_definition_version_id"
                and fk["constrained_columns"] == ["experiment_definition_version_id"]
                and fk["referred_table"] == TABLE
                for fk in audit_fks
            )
            assert "ix_audit_events_experiment_definition_version_id" in {
                i["name"] for i in inspector.get_indexes("audit_events")
            }

            with engine.connect() as connection:
                assert connection.scalar(text(f"select count(*) from {TABLE}")) == 0
                assert connection.scalar(text("select version_num from alembic_version")) == REVISION

            # Model <-> DB parity: every constraint definition the migration
            # produced is byte-identical to what the ORM metadata produced in the
            # create_all application-test database.
            assert _constraint_definitions(engine) == _constraint_definitions(postgres_engine)

            if cycle == 0:
                before = set(inspect(engine).get_table_names()) - {TABLE}
                command.downgrade(config, PREDECESSOR)
                inspector = inspect(engine)
                assert TABLE not in inspector.get_table_names()
                # Only the MVP-37 additions are removed — every other table
                # (including the neighbouring Strategy/Commercial ones) is untouched.
                assert set(inspector.get_table_names()) == before
                assert "experiments" in before and "commercial_outcomes" in before
                remaining_audit_columns = {c["name"] for c in inspector.get_columns("audit_events")}
                assert "experiment_definition_version_id" not in remaining_audit_columns
                assert "commercial_outcome_id" in remaining_audit_columns
                with engine.connect() as connection:
                    assert connection.scalar(text("select version_num from alembic_version")) == PREDECESSOR
    finally:
        engine.dispose()
        get_settings.cache_clear()
