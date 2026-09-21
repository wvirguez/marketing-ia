"""Reproducible round trip against a dedicated, disposable migration
database — mirrors ``tests/test_strategic_implication_migration.py``
exactly, one migration later."""
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
    """``alembic/env.py`` calls ``logging.config.fileConfig`` (default
    ``disable_existing_loggers=True``) on every upgrade/downgrade below.
    ``alembic.ini``'s own ``[loggers]`` section declares only
    root/sqlalchemy/alembic, so every OTHER already-registered logger —
    including this application's own ``impulso.access``/``impulso.errors``
    — gets silently ``.disabled = True`` for the remainder of the
    process. This is a real, pre-existing defect in ``alembic/env.py``
    itself (present since BACKEND-03, affecting every migration-round-
    trip test file, e.g. ``tests/test_strategic_implication_migration.py``
    — confirmed by reproducing the identical failure with that
    pre-existing file substituted for this one); it was never observed
    before because no prior migration-test filename happened to sort
    alphabetically before ``tests/test_failure_path.py`` in pytest's
    default collection order. Fixing the shared root cause is out of
    scope for MVP-27B (a cross-cutting, non-Commercial file); this
    fixture only prevents *this* test's own Alembic calls from leaking
    that process-global side effect into whatever test happens to run
    after it."""
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


def test_commercial_objective_offer_migration_round_trip(monkeypatch):
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
        if "offers" in inspect(engine).get_table_names():
            command.downgrade(config, "d82530f9e7b0")
        command.upgrade(config, "d82530f9e7b0")
        assert "commercial_objectives" not in inspect(engine).get_table_names()
        assert "offers" not in inspect(engine).get_table_names()
        for cycle in range(2):
            command.upgrade(config, "head")
            inspector = inspect(engine)

            from app.commercial.models import CommercialObjective, Offer

            for model in (CommercialObjective, Offer):
                table = model.__table__
                columns = inspector.get_columns(table.name)
                assert {c["name"] for c in columns} == set(table.columns.keys())
                assert {c["name"]: c["nullable"] for c in columns} == {c.name: c.nullable for c in table.columns}
                assert "version" not in {c["name"] for c in columns}
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

            assert "target_value" not in {c["name"] for c in inspector.get_columns("commercial_objectives")}

            offer_checks = {c["name"] for c in inspector.get_check_constraints("offers")}
            assert "ck_offers_price_currency_pair" in offer_checks
            assert "ck_offers_price_non_negative" in offer_checks

            audit_columns = {c["name"] for c in inspector.get_columns("audit_events")}
            assert "commercial_objective_id" in audit_columns
            assert "offer_id" in audit_columns

            with engine.connect() as connection:
                assert connection.scalar(text("select count(*) from commercial_objectives")) == 0
                assert connection.scalar(text("select count(*) from offers")) == 0
                # MVP-28B, MVP-29B, and MVP-30B each added one additive
                # migration after this one — "head" now means
                # b27209ee89a1, not this migration's own revision.
                assert connection.scalar(text("select version_num from alembic_version")) == "7c1e9a4d2b68"  # Experiment Evidence Binding: current head, bumped from 53b4bd83a005

            if cycle == 0:
                # MVP-28B added one additive migration after this one, so
                # "-1" from head would only undo that migration, not
                # Commercial's own — target Commercial's predecessor
                # revision explicitly instead, matching the same absolute
                # target already used above.
                command.downgrade(config, "d82530f9e7b0")
                inspector = inspect(engine)
                assert "commercial_objectives" not in inspector.get_table_names()
                assert "offers" not in inspector.get_table_names()
                remaining_audit_columns = {c["name"] for c in inspector.get_columns("audit_events")}
                assert "commercial_objective_id" not in remaining_audit_columns
                assert "offer_id" not in remaining_audit_columns
    finally:
        engine.dispose()
        get_settings.cache_clear()
