"""Reproducible round trip against a dedicated, disposable migration
database — mirrors ``tests/test_strategy_revision_migration.py`` exactly,
one migration later. Schema-inspection only (matching every other migration
test in this project) — real DB-level CHECK-constraint *enforcement* is
separately exercised through the ORM in
``tests/test_content_plan_domain.py`` (real Postgres, real INSERT attempts,
via the same fixture helpers the rest of the domain suite already relies
on), rather than duplicated here via hand-built raw-SQL fixture chains."""
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


def test_content_plan_migration_round_trip(monkeypatch):
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
        command.downgrade(config, "b27209ee89a1")
        inspector = inspect(engine)
        assert "experiment_id" not in {c["name"] for c in inspector.get_columns("content_plans")}
        for cycle in range(2):
            command.upgrade(config, "head")
            inspector = inspect(engine)

            plan_columns = {c["name"]: c["nullable"] for c in inspector.get_columns("content_plans")}
            assert plan_columns["origin"] is False
            assert plan_columns["campaign_run_id"] is True
            assert plan_columns["stage_execution_id"] is True
            assert plan_columns["experiment_id"] is True

            checks = {c["name"] for c in inspector.get_check_constraints("content_plans")}
            assert "ck_content_plans_origin_bootstrap_fields" in checks
            assert "ck_content_plans_bootstrap_experiment_null" in checks

            indexes = {i["name"] for i in inspector.get_indexes("content_plans")}
            assert "ix_content_plans_experiment_id" in indexes
            for i in inspector.get_indexes("content_plans"):
                assert len(i["name"]) <= 63

            fks = inspector.get_foreign_keys("content_plans")
            experiment_fk = next(fk for fk in fks if "experiment_id" in fk["constrained_columns"])
            assert set(experiment_fk["constrained_columns"]) == {"experiment_id", "workspace_id"}
            assert experiment_fk["referred_table"] == "experiments"
            assert all(fk["options"].get("ondelete") != "CASCADE" for fk in fks)
            for fk in fks:
                assert len(fk["name"]) <= 63

            experiment_uniques = {u["name"] for u in inspector.get_unique_constraints("experiments")}
            assert "uq_experiments_id_workspace_id" in experiment_uniques

            with engine.connect() as connection:
                assert connection.scalar(text("select version_num from alembic_version")) == "b41d7a90c2e5"  # MVP-38: current head, bumped from 7c2e91b4d0a8

            if cycle == 0:
                command.downgrade(config, "b27209ee89a1")
                inspector = inspect(engine)
                remaining_plan_columns = {c["name"] for c in inspector.get_columns("content_plans")}
                assert "origin" not in remaining_plan_columns
                assert "experiment_id" not in remaining_plan_columns
                remaining_experiment_uniques = {
                    u["name"] for u in inspector.get_unique_constraints("experiments")
                }
                assert "uq_experiments_id_workspace_id" not in remaining_experiment_uniques
    finally:
        engine.dispose()
        get_settings.cache_clear()
