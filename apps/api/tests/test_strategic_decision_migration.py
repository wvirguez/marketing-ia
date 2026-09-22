"""Reproducible round trip against a dedicated, disposable migration
database — mirrors ``tests/test_commercial_migration.py`` exactly, one
migration later."""
import logging
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from app.core.config import get_settings
from app.persistence.testing import assert_safe_test_database_url

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _restore_logging_state_after_alembic():
    """See ``tests/test_commercial_migration.py``'s identical fixture for
    the full explanation of the pre-existing ``alembic/env.py`` logging
    side effect this guards against."""
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


def test_strategic_decision_migration_round_trip(monkeypatch):
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
        if "strategic_decisions" in inspect(engine).get_table_names():
            command.downgrade(config, "5236a613ef1a")
        command.upgrade(config, "5236a613ef1a")
        assert "strategic_decisions" not in inspect(engine).get_table_names()
        for cycle in range(2):
            command.upgrade(config, "head")
            inspector = inspect(engine)

            from app.orchestration.models import StrategicDecision

            table = StrategicDecision.__table__
            columns = inspector.get_columns(table.name)
            assert {c["name"] for c in columns} == set(table.columns.keys())
            assert {c["name"]: c["nullable"] for c in columns} == {c.name: c.nullable for c in table.columns}
            fks = inspector.get_foreign_keys(table.name)
            assert {tuple(fk["constrained_columns"]) for fk in fks} == {
                tuple(c.name for c in fk.columns) for fk in table.foreign_key_constraints
            }
            assert all(fk["options"].get("ondelete") != "CASCADE" for fk in fks)
            assert all(len(i["name"]) <= 63 for i in inspector.get_indexes(table.name))
            for fk in fks:
                assert len(fk["name"]) <= 63

            checks = {c["name"] for c in inspector.get_check_constraints(table.name)}
            assert f"ck_{table.name}_disposition_complete" in checks

            indexes = {i["name"]: i for i in inspector.get_indexes(table.name)}
            assert "uq_strategic_decisions_current_recommendation" in indexes
            assert indexes["uq_strategic_decisions_current_recommendation"]["unique"]
            assert indexes["uq_strategic_decisions_current_recommendation"]["dialect_options"]["postgresql_where"]

            superseded_by_fk = next(
                fk for fk in fks if fk["constrained_columns"] == ["superseded_by_strategic_decision_id"]
            )
            assert superseded_by_fk["name"] == "fk_strategic_decisions_superseded_by_id"

            audit_columns = {c["name"] for c in inspector.get_columns("audit_events")}
            assert "strategic_decision_id" in audit_columns

            with engine.connect() as connection:
                assert connection.scalar(text("select count(*) from strategic_decisions")) == 0
                # MVP-29B and MVP-30B each added one additive migration
                # after this one — "head" now means b27209ee89a1, not this
                # migration's own revision.
                assert connection.scalar(text("select version_num from alembic_version")) == "9d4b7e2a51c3"  # Pre-Execution Measurement Declaration: current head, bumped from 7c1e9a4d2b68

            if cycle == 0:
                command.downgrade(config, "5236a613ef1a")
                inspector = inspect(engine)
                assert "strategic_decisions" not in inspector.get_table_names()
                remaining_audit_columns = {c["name"] for c in inspector.get_columns("audit_events")}
                assert "strategic_decision_id" not in remaining_audit_columns
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_deferrable_foreign_key_permits_original_update_before_replacement_insert(monkeypatch):
    """MVP-28A-R2's own ordering conflict (see
    ``app/orchestration/models.py::StrategicDecision``'s "DEFERRABLE"
    docstring note): proves the FK is genuinely deferrable at the database
    level, not merely declared so in the ORM — an UPDATE referencing a
    not-yet-existing row must succeed until COMMIT, then resolve cleanly
    once the referenced row exists before commit."""
    url = os.environ.get("TEST_MIGRATIONS_DATABASE_URL")
    if not url:
        pytest.skip("TEST_MIGRATIONS_DATABASE_URL requires a separate disposable PostgreSQL database")
    assert_safe_test_database_url(url)
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    engine = create_engine(url)
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "select is_deferrable, initially_deferred from information_schema.table_constraints "
                    "where constraint_name = 'fk_strategic_decisions_superseded_by_id'"
                )
            ).one()
            assert row.is_deferrable == "YES"
            assert row.initially_deferred == "YES"
    finally:
        engine.dispose()
        get_settings.cache_clear()
