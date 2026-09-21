"""Reproducible round trip for the Governed Execution Start migration
(``53b4bd83a005``, down_revision ``1fe7d6577113``) against the dedicated,
disposable migration database, plus model <-> DB parity against the
``create_all`` application-test database — mirrors
``tests/test_execution_authorization_migration.py``, one migration later.
Ancestor-based from the start: asserts this revision is an ancestor of the
single head, never that it IS the head."""
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

REVISION = "53b4bd83a005"
PREDECESSOR = "1fe7d6577113"
TABLE = "execution_start_attestations"
AUDIT_FK = "fk_audit_events_execution_start_attestation_id"


@pytest.fixture(autouse=True)
def _restore_logging_state_after_alembic():
    """See ``tests/test_commercial_migration.py``: ``alembic/env.py`` disables every already-registered
    logger on each upgrade/downgrade; restore the snapshot so this file's Alembic calls do not leak it."""
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


def test_there_is_exactly_one_execution_start_migration_file():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    matching = [r for r in script.walk_revisions() if "execution_start" in (r.doc or "").lower() or "execution start" in (r.doc or "").lower()]
    assert [r.revision for r in matching] == [REVISION]


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


def test_execution_start_migration_round_trip(monkeypatch, postgres_engine):
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
        assert "execution_start_attestation_id" not in {c["name"] for c in inspector.get_columns("audit_events")}
        with engine.connect() as connection:
            assert connection.scalar(text("select version_num from alembic_version")) == PREDECESSOR
            authorizations_before = connection.scalar(text("select count(*) from execution_authorizations"))
            audit_before = connection.scalar(text("select count(*) from audit_events"))

        for cycle in range(2):
            command.upgrade(config, REVISION)
            inspector = inspect(engine)

            from app.strategy.models import ExecutionStartAttestation

            model_table = ExecutionStartAttestation.__table__
            columns = inspector.get_columns(TABLE)
            assert {c["name"] for c in columns} == set(model_table.columns.keys())
            assert {c["name"]: c["nullable"] for c in columns} == {c.name: c.nullable for c in model_table.columns}
            fks = inspector.get_foreign_keys(TABLE)
            assert {tuple(fk["constrained_columns"]) for fk in fks} == {
                tuple(c.name for c in fk.columns) for fk in model_table.foreign_key_constraints
            }
            assert all(fk["options"].get("ondelete") != "CASCADE" for fk in fks)
            assert all(len(fk["name"]) <= 63 for fk in fks)
            assert all(len(i["name"]) <= 63 for i in inspector.get_indexes(TABLE))
            assert all(len(name) <= 63 for name in _constraint_definitions(engine, TABLE))

            # Frozen design: none of these may ever exist on the table.
            assert not {
                "note", "external_reference", "created_by", "experiment_id", "definition_version_id",
                "contract_version_id", "strategy_id", "variant_id", "status", "active", "ended_at", "unit_reference",
                "cohort", "assignment", "tracking_valid", "result", "winner", "validity", "causality", "updated_at",
            } & {c["name"] for c in columns}

            assert {u["name"] for u in inspector.get_unique_constraints(TABLE)} == {
                "uq_execution_start_attestations_authorization_id",
                "uq_execution_start_attestations_workspace_client_request_id",
            }
            assert inspector.get_check_constraints(TABLE) == []  # every cross-row rule is service-level

            composite = next(
                fk for fk in fks if fk["name"] == "fk_execution_start_attestations_authorization_workspace"
            )
            assert composite["referred_table"] == "execution_authorizations"
            assert composite["constrained_columns"] == ["authorization_id", "workspace_id"]
            assert composite["referred_columns"] == ["id", "workspace_id"]  # the EXISTING candidate key, no new one

            audit_columns = {c["name"]: c["nullable"] for c in inspector.get_columns("audit_events")}
            assert audit_columns["execution_start_attestation_id"] is True
            assert any(
                fk["name"] == AUDIT_FK
                and fk["constrained_columns"] == ["execution_start_attestation_id"]
                and fk["referred_table"] == TABLE
                for fk in inspector.get_foreign_keys("audit_events")
            )
            assert "ix_audit_events_execution_start_attestation_id" in {i["name"] for i in inspector.get_indexes("audit_events")}

            with engine.connect() as connection:
                assert connection.scalar(text(f"select count(*) from {TABLE}")) == 0
                assert connection.scalar(text("select count(*) from execution_authorizations")) == authorizations_before
                assert connection.scalar(text("select count(*) from audit_events")) == audit_before
                assert connection.scalar(text("select version_num from alembic_version")) == REVISION

            # Model <-> DB parity (constraints and indexes) against the create_all application-test DB.
            assert _constraint_definitions(engine, TABLE) == _constraint_definitions(postgres_engine, TABLE)
            assert _index_definitions(engine, TABLE) == _index_definitions(postgres_engine, TABLE)

            if cycle == 0:
                command.downgrade(config, PREDECESSOR)
                inspector = inspect(engine)
                assert TABLE not in inspector.get_table_names()
                remaining = {c["name"] for c in inspector.get_columns("audit_events")}
                assert "execution_start_attestation_id" not in remaining
                assert "execution_authorization_id" in remaining  # the previous domain's own addition is intact
                assert "execution_authorizations" in inspector.get_table_names()
                with engine.connect() as connection:
                    assert connection.scalar(text("select version_num from alembic_version")) == PREDECESSOR
    finally:
        engine.dispose()
        get_settings.cache_clear()
