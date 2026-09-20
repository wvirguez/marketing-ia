"""MVP-40 reproducible round trip for the Governed Execution Authorization
migration (``1fe7d6577113``, down_revision ``2536e4a8cddc``) against the
dedicated, disposable migration database, plus model <-> DB parity against
the ``create_all`` application-test database — mirrors
``tests/test_measurement_contract_migration.py``, one migration later.
Ancestor-based from the start (MVP38C-OBS-1/MVP37C-OBS-1): asserts this
revision is an ancestor of the single head, never that it IS the head."""
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

REVISION = "1fe7d6577113"
PREDECESSOR = "2536e4a8cddc"
TABLE = "execution_authorizations"
SNAPSHOT_TABLE = "execution_authorization_variants"
VARIANTS = "experiment_variants"
VARIANT_KEY = "uq_experiment_variants_id_experiment_workspace"
ACTIVE_INDEX = "uq_execution_authorizations_experiment_active"


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
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_revision(REVISION).down_revision == PREDECESSOR
    heads = script.get_heads()
    assert len(heads) == 1
    assert REVISION in {revision.revision for revision in script.walk_revisions(base="base", head=heads[0])}


def _constraint_definitions(engine, table) -> dict[str, tuple[str, str]]:
    with engine.connect() as connection:
        return {
            row.conname: (row.contype, row.definition)
            for row in connection.execute(
                text(
                    "select conname, contype, pg_get_constraintdef(oid) as definition "
                    "from pg_constraint where conrelid = to_regclass(:table) order by conname"
                ),
                {"table": table},
            )
        }


def _index_definitions(engine, table) -> dict[str, str]:
    with engine.connect() as connection:
        return {
            row.indexname: row.indexdef
            for row in connection.execute(
                text("select indexname, indexdef from pg_indexes where tablename = :table order by indexname"),
                {"table": table},
            )
        }


def test_execution_authorization_migration_round_trip(monkeypatch, postgres_engine):
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
        if TABLE in inspect(engine).get_table_names():
            command.downgrade(config, PREDECESSOR)
        command.upgrade(config, PREDECESSOR)
        inspector = inspect(engine)
        assert TABLE not in inspector.get_table_names()
        assert SNAPSHOT_TABLE not in inspector.get_table_names()
        assert "execution_authorization_id" not in {c["name"] for c in inspector.get_columns("audit_events")}
        assert VARIANT_KEY not in {u["name"] for u in inspector.get_unique_constraints(VARIANTS)}
        with engine.connect() as connection:
            assert connection.scalar(text("select version_num from alembic_version")) == PREDECESSOR

        for cycle in range(2):
            command.upgrade(config, REVISION)
            inspector = inspect(engine)

            from app.strategy.models import ExecutionAuthorization, ExecutionAuthorizationVariant

            for name, model_table in (
                (TABLE, ExecutionAuthorization.__table__), (SNAPSHOT_TABLE, ExecutionAuthorizationVariant.__table__)
            ):
                columns = inspector.get_columns(name)
                assert {c["name"] for c in columns} == set(model_table.columns.keys())
                assert {c["name"]: c["nullable"] for c in columns} == {c.name: c.nullable for c in model_table.columns}
                fks = inspector.get_foreign_keys(name)
                assert {tuple(fk["constrained_columns"]) for fk in fks} == {
                    tuple(c.name for c in fk.columns) for fk in model_table.foreign_key_constraints
                }
                assert all(fk["options"].get("ondelete") != "CASCADE" for fk in fks)
                assert all(len(fk["name"]) <= 63 for fk in fks)
                assert all(len(i["name"]) <= 63 for i in inspector.get_indexes(name))

            # Frozen design: none of these may ever exist on either table.
            columns = {c["name"] for c in inspector.get_columns(TABLE)}
            assert not {
                "status", "execution_started", "assignment_started", "exposure_started", "tracking_valid",
                "measurement_ready", "result", "winner", "loser", "validity", "causality", "strategy_id",
                "target_id", "tracking_requirement_id", "content_plan_id", "distribution_id",
            } & columns
            snapshot_columns = {c["name"] for c in inspector.get_columns(SNAPSHOT_TABLE)}
            assert not {"public_id", "ordinal", "label", "condition_description"} & snapshot_columns

            assert {u["name"] for u in inspector.get_unique_constraints(TABLE)} == {
                "uq_execution_authorizations_id_workspace",
                "uq_execution_authorizations_workspace_client_request_id",
            }
            assert {u["name"] for u in inspector.get_unique_constraints(SNAPSHOT_TABLE)} == {
                "uq_execution_authorization_variants_authorization_variant",
            }
            assert {c["name"] for c in inspector.get_check_constraints(TABLE)} == {
                "ck_execution_authorizations_revocation_pairing",
                "ck_execution_authorizations_text_fields_nonblank",
            }

            # The frozen partial unique index — the actual single-active DB backstop.
            index_def = _index_definitions(engine, TABLE)[ACTIVE_INDEX]
            assert "UNIQUE" in index_def and "(experiment_id)" in index_def and "revoked_at IS NULL" in index_def

            # Additive candidate key on experiment_variants.
            assert VARIANT_KEY in {u["name"] for u in inspector.get_unique_constraints(VARIANTS)}

            composites = {
                fk["name"]: fk for fk in inspector.get_foreign_keys(TABLE) if len(fk["constrained_columns"]) == 3
            }
            assert set(composites) == {
                "fk_execution_authorizations_definition_version_workspace",
                "fk_execution_authorizations_contract_version_workspace",
            }
            assert composites["fk_execution_authorizations_definition_version_workspace"]["referred_table"] == (
                "experiment_definition_versions"
            )
            assert composites["fk_execution_authorizations_contract_version_workspace"]["referred_table"] == (
                "measurement_contract_versions"
            )
            for composite in composites.values():
                assert composite["referred_columns"] == ["id", "experiment_id", "workspace_id"]
            experiment_fk = next(
                fk for fk in inspector.get_foreign_keys(TABLE) if fk["name"] == "fk_execution_authorizations_experiment_workspace"
            )
            assert experiment_fk["referred_table"] == "experiments" and experiment_fk["referred_columns"] == ["id", "workspace_id"]
            variant_fk = next(
                fk for fk in inspector.get_foreign_keys(SNAPSHOT_TABLE)
                if fk["name"] == "fk_execution_authorization_variants_variant_workspace"
            )
            assert variant_fk["referred_table"] == VARIANTS
            assert variant_fk["referred_columns"] == ["id", "experiment_id", "workspace_id"]
            auth_fk = next(
                fk for fk in inspector.get_foreign_keys(SNAPSHOT_TABLE)
                if fk["name"] == "fk_execution_authorization_variants_authorization_workspace"
            )
            assert auth_fk["referred_table"] == TABLE and auth_fk["referred_columns"] == ["id", "workspace_id"]
            self_fk = next(
                fk for fk in inspector.get_foreign_keys(TABLE) if fk["name"] == "fk_execution_authorizations_superseded_by_id"
            )
            assert self_fk["referred_table"] == TABLE and self_fk["constrained_columns"] == [
                "superseded_by_execution_authorization_id"
            ]

            audit_columns = {c["name"]: c["nullable"] for c in inspector.get_columns("audit_events")}
            assert audit_columns["execution_authorization_id"] is True
            assert any(
                fk["name"] == "fk_audit_events_execution_authorization_id"
                and fk["constrained_columns"] == ["execution_authorization_id"]
                and fk["referred_table"] == TABLE
                for fk in inspector.get_foreign_keys("audit_events")
            )
            assert "ix_audit_events_execution_authorization_id" in {i["name"] for i in inspector.get_indexes("audit_events")}

            with engine.connect() as connection:
                assert connection.scalar(text(f"select count(*) from {TABLE}")) == 0
                assert connection.scalar(text(f"select count(*) from {SNAPSHOT_TABLE}")) == 0
                assert connection.scalar(text("select version_num from alembic_version")) == REVISION

            # Model <-> DB parity for every table this migration touches (constraints and indexes).
            for name in (TABLE, SNAPSHOT_TABLE, VARIANTS):
                assert _constraint_definitions(engine, name) == _constraint_definitions(postgres_engine, name), name
            for name in (TABLE, SNAPSHOT_TABLE):
                assert _index_definitions(engine, name) == _index_definitions(postgres_engine, name), name

            if cycle == 0:
                before = set(inspect(engine).get_table_names()) - {TABLE, SNAPSHOT_TABLE}
                command.downgrade(config, PREDECESSOR)
                inspector = inspect(engine)
                assert TABLE not in inspector.get_table_names()
                assert SNAPSHOT_TABLE not in inspector.get_table_names()
                assert set(inspector.get_table_names()) == before
                remaining = {c["name"] for c in inspector.get_columns("audit_events")}
                assert "execution_authorization_id" not in remaining
                assert "measurement_contract_id" in remaining  # MVP-39's own addition is intact
                assert VARIANT_KEY not in {u["name"] for u in inspector.get_unique_constraints(VARIANTS)}
                with engine.connect() as connection:
                    assert connection.scalar(text("select version_num from alembic_version")) == PREDECESSOR
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_existing_rows_are_unaffected_by_the_additive_migration(monkeypatch, postgres_engine):
    """The candidate key added to ``experiment_variants`` is additive (``id`` is already unique), so
    pre-existing Variant rows survive the upgrade/downgrade cycle untouched."""
    url = os.environ.get("TEST_MIGRATIONS_DATABASE_URL")
    if not url:
        pytest.skip("TEST_MIGRATIONS_DATABASE_URL requires a separate disposable PostgreSQL database")
    assert_safe_test_database_url(url)
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    engine = create_engine(url)
    config = Config("alembic.ini")
    try:
        command.upgrade(config, PREDECESSOR) if TABLE not in inspect(engine).get_table_names() else command.downgrade(config, PREDECESSOR)
        with engine.connect() as connection:
            before = connection.scalar(text("select count(*) from experiment_variants"))
        command.upgrade(config, REVISION)
        with engine.connect() as connection:
            assert connection.scalar(text("select count(*) from experiment_variants")) == before
        command.downgrade(config, PREDECESSOR)
        with engine.connect() as connection:
            assert connection.scalar(text("select count(*) from experiment_variants")) == before
        command.upgrade(config, REVISION)
    finally:
        engine.dispose()
        get_settings.cache_clear()
