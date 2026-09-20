"""MVP-38 reproducible round trip for the Governed Variant Identity migration
(``b41d7a90c2e5``, down_revision ``7c2e91b4d0a8``) against the dedicated,
disposable migration database, plus model <-> DB parity against the
``create_all`` application-test database — mirrors
``tests/test_experiment_definition_migration.py``, one migration later."""
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

REVISION = "b41d7a90c2e5"
PREDECESSOR = "7c2e91b4d0a8"
TABLE = "experiment_variants"
VERSIONS = "experiment_definition_versions"
CANDIDATE_KEY = "uq_experiment_definition_versions_id_experiment_workspace"

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


def test_migration_is_a_single_step_after_its_predecessor_and_is_an_ancestor_of_the_single_head():
    # MVP-39 added a successor migration, so this revision is no longer the
    # head itself — it must remain a single step after its predecessor and
    # an ancestor of the one and only head (mirrors
    # test_experiment_definition_migration.py's own MVP-38C fix exactly).
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_revision(REVISION).down_revision == PREDECESSOR
    heads = script.get_heads()
    assert len(heads) == 1
    assert REVISION in {revision.revision for revision in script.walk_revisions(base="base", head=heads[0])}


def _constraint_definitions(engine, table) -> dict[str, tuple[str, str]]:
    with engine.connect() as connection:
        return {
            row.conname: (row.contype, row.definition)
            for row in connection.execute(text(_CONSTRAINT_DEFS), {"table": table})
        }


def test_variant_migration_round_trip(monkeypatch, postgres_engine):
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
        assert "experiment_variant_id" not in {c["name"] for c in inspector.get_columns("audit_events")}
        assert CANDIDATE_KEY not in {u["name"] for u in inspector.get_unique_constraints(VERSIONS)}
        with engine.connect() as connection:
            assert connection.scalar(text("select version_num from alembic_version")) == PREDECESSOR

        for cycle in range(2):
            command.upgrade(config, REVISION)
            inspector = inspect(engine)

            from app.strategy.models import ExperimentVariant

            table = ExperimentVariant.__table__
            columns = inspector.get_columns(TABLE)
            assert {c["name"] for c in columns} == set(table.columns.keys())
            assert {c["name"]: c["nullable"] for c in columns} == {c.name: c.nullable for c in table.columns}
            # Frozen MVP-38B: none of these may ever exist on the Variant table.
            assert not {
                "role", "created_by", "updated_at", "status", "allocation", "weight", "traffic", "metric",
                "success_criterion", "exposure", "winner", "result",
            } & {c["name"] for c in columns}

            fks = inspector.get_foreign_keys(TABLE)
            assert {tuple(fk["constrained_columns"]) for fk in fks} == {
                tuple(c.name for c in fk.columns) for fk in table.foreign_key_constraints
            }
            composite = next(fk for fk in fks if len(fk["constrained_columns"]) == 3)
            assert composite["name"] == "fk_experiment_variants_definition_version_experiment_workspace"
            assert composite["constrained_columns"] == ["definition_version_id", "experiment_id", "workspace_id"]
            assert composite["referred_table"] == VERSIONS
            assert composite["referred_columns"] == ["id", "experiment_id", "workspace_id"]
            assert all(fk["options"].get("ondelete") != "CASCADE" for fk in fks)
            assert all(len(fk["name"]) <= 63 for fk in fks)
            assert all(len(i["name"]) <= 63 for i in inspector.get_indexes(TABLE))

            uniques = {u["name"] for u in inspector.get_unique_constraints(TABLE)}
            assert uniques == {
                "uq_experiment_variants_definition_version_label",
                "uq_experiment_variants_definition_version_ordinal",
                "uq_experiment_variants_workspace_client_request_id",
            }
            assert CANDIDATE_KEY in {u["name"] for u in inspector.get_unique_constraints(VERSIONS)}
            index_names = {i["name"] for i in inspector.get_indexes(TABLE)}
            assert {
                "ix_experiment_variants_public_id",
                "ix_experiment_variants_workspace_id",
                "ix_experiment_variants_experiment_id",
                "ix_experiment_variants_definition_version_id",
            } <= index_names
            checks = {c["name"] for c in inspector.get_check_constraints(TABLE)}
            assert checks == {
                "ck_experiment_variants_ordinal_positive",
                "ck_experiment_variants_text_fields_nonblank",
            }

            audit_columns = {c["name"]: c["nullable"] for c in inspector.get_columns("audit_events")}
            assert audit_columns["experiment_variant_id"] is True
            assert any(
                fk["name"] == "fk_audit_events_experiment_variant_id_experiment_variants"
                and fk["constrained_columns"] == ["experiment_variant_id"]
                and fk["referred_table"] == TABLE
                for fk in inspector.get_foreign_keys("audit_events")
            )
            assert "ix_audit_events_experiment_variant_id" in {i["name"] for i in inspector.get_indexes("audit_events")}

            with engine.connect() as connection:
                assert connection.scalar(text(f"select count(*) from {TABLE}")) == 0
                assert connection.scalar(text("select version_num from alembic_version")) == REVISION

            # Model <-> DB parity for BOTH tables the migration touches.
            for name in (TABLE, VERSIONS):
                assert _constraint_definitions(engine, name) == _constraint_definitions(postgres_engine, name), name

            if cycle == 0:
                before = set(inspect(engine).get_table_names()) - {TABLE}
                command.downgrade(config, PREDECESSOR)
                inspector = inspect(engine)
                assert TABLE not in inspector.get_table_names()
                # Only the MVP-38 additions are removed; MVP-37's table and constraints are intact.
                assert set(inspector.get_table_names()) == before
                assert VERSIONS in before
                assert CANDIDATE_KEY not in {u["name"] for u in inspector.get_unique_constraints(VERSIONS)}
                assert "uq_experiment_definition_versions_experiment_version" in {
                    u["name"] for u in inspector.get_unique_constraints(VERSIONS)
                }
                remaining = {c["name"] for c in inspector.get_columns("audit_events")}
                assert "experiment_variant_id" not in remaining
                assert "experiment_definition_version_id" in remaining
                with engine.connect() as connection:
                    assert connection.scalar(text("select version_num from alembic_version")) == PREDECESSOR
    finally:
        engine.dispose()
        get_settings.cache_clear()
