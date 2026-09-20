"""Reproducible round trip against a dedicated, disposable migration
database — mirrors ``tests/test_strategic_decision_migration.py`` exactly,
one migration later."""
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


def test_strategic_approval_migration_round_trip(monkeypatch):
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
        if "strategic_approvals" in inspect(engine).get_table_names():
            command.downgrade(config, "7b4151d4cf64")
        command.upgrade(config, "7b4151d4cf64")
        assert "strategic_approvals" not in inspect(engine).get_table_names()
        for cycle in range(2):
            command.upgrade(config, "head")
            inspector = inspect(engine)

            from app.orchestration.models import StrategicApproval

            table = StrategicApproval.__table__
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
            assert "ix_strategic_approvals_strategic_decision_id" in indexes
            assert indexes["ix_strategic_approvals_strategic_decision_id"]["unique"]

            audit_columns = {c["name"] for c in inspector.get_columns("audit_events")}
            assert "strategic_approval_id" in audit_columns

            with engine.connect() as connection:
                assert connection.scalar(text("select count(*) from strategic_approvals")) == 0
                # MVP-30B added one additive migration after this one —
                # "head" now means b27209ee89a1, not this migration's own
                # revision.
                assert connection.scalar(text("select version_num from alembic_version")) == "2536e4a8cddc"  # MVP-39: current head, bumped from b41d7a90c2e5

            if cycle == 0:
                command.downgrade(config, "7b4151d4cf64")
                inspector = inspect(engine)
                assert "strategic_approvals" not in inspector.get_table_names()
                remaining_audit_columns = {c["name"] for c in inspector.get_columns("audit_events")}
                assert "strategic_approval_id" not in remaining_audit_columns
    finally:
        engine.dispose()
        get_settings.cache_clear()
