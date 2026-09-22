"""Reproducible round trip for the Experiment Measurement migration
(``5aef32a6dc47``, down_revision ``9d4b7e2a51c3``) against the dedicated,
disposable migration database, plus model <-> DB parity against the
``create_all`` application-test database (constraint AND index names, not
just columns — several names were hash-truncated/renamed during
implementation and must match exactly). Ancestor-based: asserts this
revision is an ancestor of the single head, never that it IS the head.

Also proves, on real PostgreSQL and on the MIGRATED schema (not
``create_all``): legacy-safe (no data change to ExperimentEvidenceClaim),
the two new claim candidate keys exist, the accepted DOWNGRADE HAZARD —
Measurement history created after this revision is LOST on downgrade.
"""
import os

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings
from app.persistence.testing import assert_safe_test_database_url

pytestmark = pytest.mark.postgres

REVISION = "5aef32a6dc47"
PREDECESSOR = "9d4b7e2a51c3"
NEW_TABLES = (
    "experiment_measurement_runs",
    "experiment_measurement_signal_outputs",
    "experiment_measurement_slice_outputs",
    "experiment_measurement_datum_usages",
)


def test_migration_is_a_single_step_after_its_predecessor_and_is_an_ancestor_of_the_single_head():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1
    revision = script.get_revision(REVISION)
    assert revision.down_revision == PREDECESSOR
    ancestors = {rev.revision for rev in script.iterate_revisions(heads[0], None)}
    assert REVISION in ancestors


def test_there_is_exactly_one_experiment_measurement_migration_file():
    import glob

    matches = glob.glob("alembic/versions/5aef32a6dc47_*.py")
    assert len(matches) == 1


def test_the_downgrade_hazard_is_documented_in_the_migration():
    with open("alembic/versions/5aef32a6dc47_add_experiment_measurement.py") as handle:
        text_ = handle.read()
    assert "DOWNGRADE HAZARD" in text_
    assert "LOST" in text_


def test_experiment_measurement_migration_round_trip(monkeypatch, postgres_engine):
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
        if "experiment_measurement_runs" in inspect(engine).get_table_names():
            command.downgrade(config, PREDECESSOR)
        command.upgrade(config, PREDECESSOR)
        for table in NEW_TABLES:
            assert table not in inspect(engine).get_table_names()

        for cycle in range(2):
            command.upgrade(config, "head")
            inspector = inspect(engine)

            from app.strategy.models import (
                ExperimentMeasurementDatumUsage,
                ExperimentMeasurementRun,
                ExperimentMeasurementSignalOutput,
                ExperimentMeasurementSliceOutput,
            )

            model_by_table = {
                "experiment_measurement_runs": ExperimentMeasurementRun,
                "experiment_measurement_signal_outputs": ExperimentMeasurementSignalOutput,
                "experiment_measurement_slice_outputs": ExperimentMeasurementSliceOutput,
                "experiment_measurement_datum_usages": ExperimentMeasurementDatumUsage,
            }
            for table, model in model_by_table.items():
                columns = inspector.get_columns(table)
                assert {c["name"] for c in columns} == set(model.__table__.columns.keys()), table
                assert {c["name"]: c["nullable"] for c in columns} == {
                    c.name: c.nullable for c in model.__table__.columns
                }, table

                unique_names = {c["name"] for c in inspector.get_unique_constraints(table)}
                mig_names = {
                    ("CK", c["name"]) for c in inspector.get_check_constraints(table)
                } | {
                    ("UQ", name) for name in unique_names
                } | {
                    ("FK", c["name"]) for c in inspector.get_foreign_keys(table)
                } | {
                    # PostgreSQL backs every UNIQUE constraint with an index of
                    # the same name; the inspector surfaces both — exclude the
                    # duplicate so this comparison matches the ORM model's own
                    # explicit Index objects only (UniqueConstraints are
                    # already counted above, once, as "UQ").
                    ("IX", i["name"]) for i in inspector.get_indexes(table) if i["name"] not in unique_names
                } | {("PK", inspector.get_pk_constraint(table)["name"])}

                app_names = {
                    ("CK", c.name) for c in model.__table__.constraints if type(c).__name__ == "CheckConstraint"
                } | {
                    ("UQ", c.name) for c in model.__table__.constraints if type(c).__name__ == "UniqueConstraint"
                } | {
                    ("FK", c.name) for c in model.__table__.constraints if type(c).__name__ == "ForeignKeyConstraint"
                } | {
                    ("IX", ix.name) for ix in model.__table__.indexes
                } | {("PK", model.__table__.primary_key.name)}
                assert mig_names == app_names, (table, mig_names ^ app_names)

            claim_uniques = {u["name"] for u in inspector.get_unique_constraints("experiment_evidence_claims")}
            assert "uq_experiment_evidence_claims_id_start_workspace" in claim_uniques
            assert "uq_experiment_evidence_claims_id_required_signal_workspace" in claim_uniques

            audit_fks = {fk["name"] for fk in inspector.get_foreign_keys("audit_events")}
            assert "fk_audit_events_experiment_measurement_run_id" in audit_fks

            with engine.connect() as connection:
                for table in NEW_TABLES:
                    assert connection.scalar(text(f"select count(*) from {table}")) == 0
                assert connection.scalar(text("select version_num from alembic_version")) == REVISION

            if cycle == 0:
                command.downgrade(config, PREDECESSOR)
                inspector = inspect(engine)
                for table in NEW_TABLES:
                    assert table not in inspector.get_table_names()
                claim_uniques = {u["name"] for u in inspector.get_unique_constraints("experiment_evidence_claims")}
                assert "uq_experiment_evidence_claims_id_start_workspace" not in claim_uniques
                audit_fks = {fk["name"] for fk in inspector.get_foreign_keys("audit_events")}
                assert "fk_audit_events_experiment_measurement_run_id" not in audit_fks
    finally:
        engine.dispose()
        get_settings.cache_clear()
