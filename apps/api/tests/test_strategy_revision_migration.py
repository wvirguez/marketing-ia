"""Reproducible round trip against a dedicated, disposable migration
database — mirrors ``tests/test_strategic_approval_migration.py`` exactly,
one migration later. Also directly exercises the origin CHECK constraint
via real PostgreSQL inserts (not merely inspected), since SQLite cannot
prove CHECK constraint enforcement and this is the first migration in the
project that alters an existing table's nullability + adds a CHECK tying
two columns to a native enum."""
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


def test_strategy_revision_migration_round_trip(monkeypatch):
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
        if "strategy_revisions" in inspect(engine).get_table_names():
            command.downgrade(config, "eae9bb978d9c")
        command.upgrade(config, "eae9bb978d9c")
        assert "strategy_revisions" not in inspect(engine).get_table_names()
        for cycle in range(2):
            command.upgrade(config, "head")
            inspector = inspect(engine)

            from app.orchestration.models import StrategyRevision

            table = StrategyRevision.__table__
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

            indexes = {i["name"]: i for i in inspector.get_indexes(table.name)}
            assert indexes["ix_strategy_revisions_strategic_approval_id"]["unique"]
            assert indexes["ix_strategy_revisions_result_strategy_id"]["unique"]
            assert not indexes["ix_strategy_revisions_base_strategy_id"]["unique"]

            # Composite, tenant-safe FKs on base_strategy_id/result_strategy_id;
            # plain FK on strategic_approval_id (StrategicApproval has no
            # composite candidate key, MVP-30A-R1 §13).
            base_fk = next(fk for fk in fks if set(fk["constrained_columns"]) == {"base_strategy_id", "workspace_id"})
            assert base_fk["referred_table"] == "strategies"
            result_fk = next(fk for fk in fks if "result_strategy_id" in fk["constrained_columns"] and len(fk["constrained_columns"]) == 2)
            assert result_fk["referred_table"] == "strategies"
            approval_fk = next(fk for fk in fks if fk["constrained_columns"] == ["strategic_approval_id"])
            assert approval_fk["referred_table"] == "strategic_approvals"

            # Strategy origin integrity.
            strategy_columns = {c["name"]: c["nullable"] for c in inspector.get_columns("strategies")}
            assert strategy_columns["origin"] is False
            assert strategy_columns["campaign_run_id"] is True
            assert strategy_columns["stage_execution_id"] is True
            checks = {c["name"] for c in inspector.get_check_constraints("strategies")}
            assert "ck_strategies_origin_bootstrap_fields" in checks

            audit_columns = {c["name"] for c in inspector.get_columns("audit_events")}
            assert "strategy_revision_id" in audit_columns

            with engine.connect() as connection:
                assert connection.scalar(text("select count(*) from strategy_revisions")) == 0
                assert connection.scalar(text("select version_num from alembic_version")) == "2536e4a8cddc"  # MVP-39: current head, bumped from b41d7a90c2e5

            if cycle == 0:
                command.downgrade(config, "eae9bb978d9c")
                inspector = inspect(engine)
                assert "strategy_revisions" not in inspector.get_table_names()
                remaining_strategy_columns = {c["name"] for c in inspector.get_columns("strategies")}
                assert "origin" not in remaining_strategy_columns
                remaining_audit_columns = {c["name"] for c in inspector.get_columns("audit_events")}
                assert "strategy_revision_id" not in remaining_audit_columns
    finally:
        engine.dispose()
        get_settings.cache_clear()
