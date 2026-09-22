"""MVP-39 reproducible round trip for the Governed Measurement Contract
migration (``2536e4a8cddc``, down_revision ``b41d7a90c2e5``) against the
dedicated, disposable migration database, plus model <-> DB parity against
the ``create_all`` application-test database — mirrors
``tests/test_experiment_variant_migration.py``, one migration later.
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

REVISION = "2536e4a8cddc"
PREDECESSOR = "b41d7a90c2e5"
TABLE = "measurement_contract_versions"
SIGNAL_TABLE = "measurement_contract_signals"
DEFINITIONS = "experiment_definition_versions"


# Experiment Evidence Binding (7c1e9a4d2b68) later added this UNIQUE to a table created by THIS revision. This test
# migrates only to its own revision, so the create_all application-test DB (current models) legitimately carries one
# extra constraint; it is excluded from the parity comparison rather than the parity assertion being weakened.
LATER_CONSTRAINTS = {"uq_contract_signals_id_contract_experiment_workspace"}
# Pre-Execution Measurement Declaration (9d4b7e2a51c3) later added nullable columns, CHECKs and one unique expression index to
# the two tables created by THIS revision; the same exclusion applies (columns are excluded from the column-set parity).
LATER_COLUMNS = {
    "measurement_contract_versions": {"declaration_level", "declaration_semantics_version", "baseline_window_days"},
    "measurement_contract_signals": {"bound_metric_name", "channel_binding", "bound_channel", "min_data_points"},
}
LATER_CONSTRAINTS |= {
    "ck_measurement_contract_versions_" + n
    for n in (
        "declaration_level_valid", "level_version_copresent", "semantics_version_v1",
        "structured_window_bounds", "baseline_iff_comparative", "baseline_window_bounds",
    )
} | {
    "ck_measurement_contract_signals_" + n
    for n in (
        "binding_copresent", "channel_binding_valid", "bound_channel_consistent",
        "binding_text_nonblank_trimmed", "min_data_points_positive", "min_points_requires_binding",
    )
}
LATER_INDEXES = {"uq_contract_signals_binding_slot"}


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


def test_measurement_contract_migration_round_trip(monkeypatch, postgres_engine):
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
        assert SIGNAL_TABLE not in inspector.get_table_names()
        assert "measurement_contract_id" not in {c["name"] for c in inspector.get_columns("audit_events")}
        with engine.connect() as connection:
            assert connection.scalar(text("select version_num from alembic_version")) == PREDECESSOR

        for cycle in range(2):
            command.upgrade(config, REVISION)
            inspector = inspect(engine)

            from app.strategy.models import MeasurementContractRequiredSignal, MeasurementContractVersion

            for name, model_table in ((TABLE, MeasurementContractVersion.__table__), (SIGNAL_TABLE, MeasurementContractRequiredSignal.__table__)):
                columns = inspector.get_columns(name)
                later = LATER_COLUMNS.get(name, set())
                assert {c["name"] for c in columns} == set(model_table.columns.keys()) - later
                assert {c["name"]: c["nullable"] for c in columns} == {
                    c.name: c.nullable for c in model_table.columns if c.name not in later
                }
                fks = inspector.get_foreign_keys(name)
                assert {tuple(fk["constrained_columns"]) for fk in fks} == {
                    tuple(c.name for c in fk.columns) for fk in model_table.foreign_key_constraints
                }
                assert all(fk["options"].get("ondelete") != "CASCADE" for fk in fks)
                assert all(len(fk["name"]) <= 63 for fk in fks)
                assert all(len(i["name"]) <= 63 for i in inspector.get_indexes(name))

            # Frozen MVP-39B: none of these may ever exist on either table.
            contract_columns = {c["name"] for c in inspector.get_columns(TABLE)}
            assert not {"status", "frozen_at", "execution_authorized", "winner", "result"} & contract_columns
            signal_columns = {c["name"] for c in inspector.get_columns(SIGNAL_TABLE)}
            assert not {"observed_value", "score", "threshold", "operator", "metric_entry_id"} & signal_columns

            uniques = {u["name"] for u in inspector.get_unique_constraints(TABLE)}
            assert uniques == {
                "uq_measurement_contract_versions_experiment_version",
                "uq_measurement_contract_versions_id_experiment_workspace",
                "uq_measurement_contract_versions_workspace_client_request_id",
            }
            signal_uniques = {u["name"] for u in inspector.get_unique_constraints(SIGNAL_TABLE)}
            assert signal_uniques == {
                "uq_measurement_contract_signals_contract_version_ordinal",
                "uq_measurement_contract_signals_contract_version_name",
            }
            checks = {c["name"] for c in inspector.get_check_constraints(TABLE)}
            assert checks == {
                "ck_measurement_contract_versions_version_positive",
                "ck_measurement_contract_versions_window_days_positive",
                "ck_measurement_contract_versions_optional_text_nonblank",
            }
            signal_checks = {c["name"] for c in inspector.get_check_constraints(SIGNAL_TABLE)}
            assert signal_checks == {
                "ck_measurement_contract_signals_ordinal_positive",
                "ck_measurement_contract_signals_text_fields_nonblank",
                "ck_measurement_contract_signals_expected_direction_valid",
                "ck_measurement_contract_signals_evidence_nonblank_if_present",
            }

            composite = next(fk for fk in inspector.get_foreign_keys(TABLE) if len(fk["constrained_columns"]) == 3)
            assert composite["name"] == "fk_measurement_contract_versions_definition_version_workspace"
            assert composite["referred_table"] == DEFINITIONS
            assert composite["referred_columns"] == ["id", "experiment_id", "workspace_id"]

            signal_composite = next(fk for fk in inspector.get_foreign_keys(SIGNAL_TABLE) if len(fk["constrained_columns"]) == 3)
            assert signal_composite["name"] == "fk_measurement_contract_signals_contract_version_workspace"
            assert signal_composite["referred_table"] == TABLE
            assert signal_composite["referred_columns"] == ["id", "experiment_id", "workspace_id"]

            # No new candidate key added to experiment_definition_versions this migration.
            assert "uq_experiment_definition_versions_id_experiment_workspace" in {
                u["name"] for u in inspector.get_unique_constraints(DEFINITIONS)
            }

            audit_columns = {c["name"]: c["nullable"] for c in inspector.get_columns("audit_events")}
            assert audit_columns["measurement_contract_id"] is True
            assert any(
                fk["name"] == "fk_audit_events_measurement_contract_id"
                and fk["constrained_columns"] == ["measurement_contract_id"]
                and fk["referred_table"] == TABLE
                for fk in inspector.get_foreign_keys("audit_events")
            )
            assert "ix_audit_events_measurement_contract_id" in {i["name"] for i in inspector.get_indexes("audit_events")}

            with engine.connect() as connection:
                assert connection.scalar(text(f"select count(*) from {TABLE}")) == 0
                assert connection.scalar(text(f"select count(*) from {SIGNAL_TABLE}")) == 0
                assert connection.scalar(text("select version_num from alembic_version")) == REVISION

            # Model <-> DB parity for every table this migration touches.
            for name in (TABLE, SIGNAL_TABLE, DEFINITIONS):
                assert _constraint_definitions(engine, name) == {
                    constraint: definition
                    for constraint, definition in _constraint_definitions(postgres_engine, name).items()
                    if constraint not in LATER_CONSTRAINTS
                }, name

            if cycle == 0:
                before = set(inspect(engine).get_table_names()) - {TABLE, SIGNAL_TABLE}
                command.downgrade(config, PREDECESSOR)
                inspector = inspect(engine)
                assert TABLE not in inspector.get_table_names()
                assert SIGNAL_TABLE not in inspector.get_table_names()
                assert set(inspector.get_table_names()) == before
                remaining = {c["name"] for c in inspector.get_columns("audit_events")}
                assert "measurement_contract_id" not in remaining
                assert "experiment_variant_id" in remaining  # MVP-38's own addition is intact
                with engine.connect() as connection:
                    assert connection.scalar(text("select version_num from alembic_version")) == PREDECESSOR
    finally:
        engine.dispose()
        get_settings.cache_clear()
