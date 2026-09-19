"""MVP-36B-R1 (D1): reproducible round trip for the MVP-36 CommercialOutcome
migration (``41687fa37c1c``, down_revision ``cdaeec1bb87f``) against the
dedicated, disposable migration database — mirrors
``tests/test_commercial_migration.py``, one migration later."""
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

REVISION = "41687fa37c1c"
PREDECESSOR = "cdaeec1bb87f"


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


def test_commercial_outcome_migration_round_trip(monkeypatch):
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
        if "commercial_outcomes" in inspect(engine).get_table_names():
            command.downgrade(config, PREDECESSOR)
        command.upgrade(config, PREDECESSOR)
        inspector = inspect(engine)
        assert "commercial_outcomes" not in inspector.get_table_names()
        assert "commercial_outcome_id" not in {c["name"] for c in inspector.get_columns("audit_events")}
        with engine.connect() as connection:
            assert connection.scalar(text("select version_num from alembic_version")) == PREDECESSOR

        for cycle in range(2):
            command.upgrade(config, "head")
            inspector = inspect(engine)

            from app.commercial.models import CommercialOutcome

            table = CommercialOutcome.__table__
            columns = inspector.get_columns(table.name)
            assert {c["name"] for c in columns} == set(table.columns.keys())
            assert {c["name"]: c["nullable"] for c in columns} == {c.name: c.nullable for c in table.columns}
            # Frozen MVP-36A/-R1 contract: no experiment/variant/objective/
            # offer/tracking/attribution/status column may ever exist here.
            assert not {
                "experiment_id",
                "variant_id",
                "commercial_objective_id",
                "offer_id",
                "tracking_requirement_id",
                "status",
                "updated_at",
            } & {c["name"] for c in columns}

            fks = inspector.get_foreign_keys(table.name)
            assert {tuple(fk["constrained_columns"]) for fk in fks} == {
                tuple(c.name for c in fk.columns) for fk in table.foreign_key_constraints
            }
            fk_names = {fk["name"] for fk in fks}
            assert {
                "fk_commercial_outcomes_campaign_workspace",
                "fk_commercial_outcomes_distribution_workspace",
                "fk_commercial_outcomes_supersedes_same_campaign",
            } <= fk_names
            assert all(fk["options"].get("ondelete") != "CASCADE" for fk in fks)
            assert all(len(fk["name"]) <= 63 for fk in fks)
            assert all(len(i["name"]) <= 63 for i in inspector.get_indexes(table.name))

            uniques = {u["name"] for u in inspector.get_unique_constraints(table.name)}
            assert {
                "uq_commercial_outcomes_id_campaign_workspace",
                "uq_commercial_outcomes_workspace_client_request_id",
                "uq_commercial_outcomes_supersedes_outcome_id",
            } <= uniques

            checks = {c["name"] for c in inspector.get_check_constraints(table.name)}
            assert {
                "ck_commercial_outcomes_quantity_positive",
                "ck_commercial_outcomes_monetary_value_currency_pair",
                "ck_commercial_outcomes_monetary_value_non_negative",
                "ck_commercial_outcomes_correction_reason_pairing",
            } <= checks

            audit_columns = {c["name"]: c["nullable"] for c in inspector.get_columns("audit_events")}
            assert audit_columns["commercial_outcome_id"] is True
            audit_fks = inspector.get_foreign_keys("audit_events")
            assert any(
                fk["constrained_columns"] == ["commercial_outcome_id"] and fk["referred_table"] == "commercial_outcomes"
                for fk in audit_fks
            )

            with engine.connect() as connection:
                assert connection.scalar(text("select count(*) from commercial_outcomes")) == 0
                assert connection.scalar(text("select version_num from alembic_version")) == REVISION

            if cycle == 0:
                before = set(inspect(engine).get_table_names()) - {"commercial_outcomes"}
                command.downgrade(config, PREDECESSOR)
                inspector = inspect(engine)
                assert "commercial_outcomes" not in inspector.get_table_names()
                # Only the MVP-36 additions are removed — every other table
                # (including the neighbouring Commercial ones) is untouched.
                assert set(inspector.get_table_names()) == before
                assert "commercial_objectives" in before and "offers" in before
                remaining_audit_columns = {c["name"] for c in inspector.get_columns("audit_events")}
                assert "commercial_outcome_id" not in remaining_audit_columns
                assert "commercial_objective_id" in remaining_audit_columns
                assert "offer_id" in remaining_audit_columns
                with engine.connect() as connection:
                    assert connection.scalar(text("select version_num from alembic_version")) == PREDECESSOR
    finally:
        engine.dispose()
        get_settings.cache_clear()
