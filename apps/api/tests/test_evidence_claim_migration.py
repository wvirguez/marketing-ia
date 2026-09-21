"""Reproducible round trip for the Experiment Evidence Binding migration
(``7c1e9a4d2b68``, down_revision ``53b4bd83a005``) against the dedicated,
disposable migration database, plus model <-> DB parity against the
``create_all`` application-test database — mirrors
``tests/test_execution_start_migration.py``, one migration later.
Ancestor-based from the start: asserts this revision is an ancestor of the
single head, never that it IS the head.

Also proves the LOAD-BEARING duplicate pre-check (EEB-IMPL-IC1): if
``metric_values`` already holds a duplicate ``(metric_entry_id, metric_name)``
the upgrade FAILS LOUDLY and changes nothing — no merge, no delete, no repair,
no silent selection.
"""
import logging
import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.persistence.testing import assert_safe_test_database_url

pytestmark = pytest.mark.postgres

REVISION = "7c1e9a4d2b68"
PREDECESSOR = "53b4bd83a005"
TABLE = "experiment_evidence_claims"
AUDIT_FK = "fk_audit_events_experiment_evidence_claim_id"
NEW_UNIQUES = {
    "metric_values": ("uq_metric_values_metric_entry_id_metric_name", ["metric_entry_id", "metric_name"]),
    "measurement_contract_signals": (
        "uq_contract_signals_id_contract_experiment_workspace",
        ["id", "contract_version_id", "experiment_id", "workspace_id"],
    ),
    "execution_authorizations": (
        "uq_execution_authorizations_id_contract_experiment_workspace",
        ["id", "contract_version_id", "experiment_id", "workspace_id"],
    ),
    "execution_start_attestations": (
        "uq_execution_start_attestations_id_authorization_workspace",
        ["id", "authorization_id", "workspace_id"],
    ),
}


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


def _migration_engine(monkeypatch):
    url = os.environ.get("TEST_MIGRATIONS_DATABASE_URL")
    if not url:
        pytest.skip("TEST_MIGRATIONS_DATABASE_URL requires a separate disposable PostgreSQL database")
    assert_safe_test_database_url(url)
    assert url != os.environ.get("TEST_DATABASE_URL"), "Migration DB must be isolated from application tests"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    return create_engine(url)


def test_migration_is_a_single_step_after_its_predecessor_and_is_an_ancestor_of_the_single_head():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_revision(REVISION).down_revision == PREDECESSOR
    heads = script.get_heads()
    assert len(heads) == 1
    assert REVISION in {revision.revision for revision in script.walk_revisions(base="base", head=heads[0])}


def test_there_is_exactly_one_evidence_claim_migration_file():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    matching = [
        r for r in script.walk_revisions()
        if "experiment evidence binding" in (r.doc or "").lower()
    ]
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


def _unique_names(engine, table) -> set[str]:
    return {u["name"] for u in inspect(engine).get_unique_constraints(table)}


def test_evidence_claim_migration_round_trip(monkeypatch, postgres_engine):
    engine = _migration_engine(monkeypatch)
    config = Config("alembic.ini")
    try:
        if TABLE in inspect(engine).get_table_names():
            command.downgrade(config, PREDECESSOR)
        command.upgrade(config, PREDECESSOR)
        inspector = inspect(engine)
        assert TABLE not in inspector.get_table_names()
        assert "experiment_evidence_claim_id" not in {c["name"] for c in inspector.get_columns("audit_events")}
        for table, (name, _columns) in NEW_UNIQUES.items():
            assert name not in _unique_names(engine, table)  # none of the four keys exists before this revision
        with engine.connect() as connection:
            assert connection.scalar(text("select version_num from alembic_version")) == PREDECESSOR
            starts_before = connection.scalar(text("select count(*) from execution_start_attestations"))
            values_before = connection.scalar(text("select count(*) from metric_values"))
            audit_before = connection.scalar(text("select count(*) from audit_events"))

        for cycle in range(2):
            command.upgrade(config, REVISION)
            inspector = inspect(engine)

            from app.strategy.models import ExperimentEvidenceClaim

            model_table = ExperimentEvidenceClaim.__table__
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
                "variant_id", "assignment_id", "exposure_id", "campaign_id", "strategy_id", "note", "status",
                "eligible", "eligibility", "valid", "validity", "result", "winner", "verdict", "value", "period_start",
                "period_end", "channel", "source", "updated_at", "is_current",
            } & {c["name"] for c in columns}

            # Exactly the four frozen candidate keys, each with its frozen columns and a <= 63-character name.
            for table, (name, expected_columns) in NEW_UNIQUES.items():
                uniques = {u["name"]: u["column_names"] for u in inspector.get_unique_constraints(table)}
                assert uniques[name] == expected_columns, table
                assert all(len(n) <= 63 for n in uniques)

            audit_columns = {c["name"]: c["nullable"] for c in inspector.get_columns("audit_events")}
            assert audit_columns["experiment_evidence_claim_id"] is True
            assert any(
                fk["name"] == AUDIT_FK
                and fk["constrained_columns"] == ["experiment_evidence_claim_id"]
                and fk["referred_table"] == TABLE
                for fk in inspector.get_foreign_keys("audit_events")
            )
            assert "ix_audit_events_experiment_evidence_claim_id" in {i["name"] for i in inspector.get_indexes("audit_events")}
            assert "uq_experiment_evidence_claims_active_datum" in {i["name"] for i in inspector.get_indexes(TABLE)}

            with engine.connect() as connection:
                assert connection.scalar(text(f"select count(*) from {TABLE}")) == 0  # additive, no backfill
                assert connection.scalar(text("select count(*) from execution_start_attestations")) == starts_before
                assert connection.scalar(text("select count(*) from metric_values")) == values_before
                assert connection.scalar(text("select count(*) from audit_events")) == audit_before
                assert connection.scalar(text("select version_num from alembic_version")) == REVISION

            # Model <-> DB parity (constraints and indexes) against the create_all application-test DB — for the
            # new table AND for every existing table that gained a candidate key.
            for table in (TABLE, *NEW_UNIQUES):
                assert _constraint_definitions(engine, table) == _constraint_definitions(postgres_engine, table), table
                assert _index_definitions(engine, table) == _index_definitions(postgres_engine, table), table

            if cycle == 0:
                command.downgrade(config, PREDECESSOR)
                inspector = inspect(engine)
                assert TABLE not in inspector.get_table_names()
                remaining = {c["name"] for c in inspector.get_columns("audit_events")}
                assert "experiment_evidence_claim_id" not in remaining
                assert "execution_start_attestation_id" in remaining  # the previous domain's own addition is intact
                for table, (name, _columns) in NEW_UNIQUES.items():
                    assert name not in _unique_names(engine, table)  # all four keys are dropped again
                # ...and the predecessor's own pre-existing keys are intact.
                assert "uq_execution_authorizations_id_workspace" in _unique_names(engine, "execution_authorizations")
                assert "uq_execution_start_attestations_authorization_id" in _unique_names(
                    engine, "execution_start_attestations"
                )
                with engine.connect() as connection:
                    assert connection.scalar(text("select version_num from alembic_version")) == PREDECESSOR
    finally:
        engine.dispose()
        get_settings.cache_clear()


# --- EEB-IMPL-IC1: the duplicate pre-check --------------------------------------------------------------------------------


def _seed_duplicate_metric_values(engine) -> dict:
    """At the PREDECESSOR revision (no UNIQUE yet) insert one MetricEntry whose metric name appears TWICE."""
    from app.campaigns.repository import CampaignRepository
    from app.measurement.models import MetricSource, MetricValue
    from app.measurement.repository import MetricEntryRepository
    from app.workspaces.repository import OrganizationRepository, WorkspaceRepository

    with Session(engine) as session:
        organization = OrganizationRepository(session).create(name=f"Dup Org {uuid.uuid4().hex[:8]}")
        workspace = WorkspaceRepository(session).create(organization_id=organization.id, name="Dup WS")
        campaign = CampaignRepository(session).create(workspace_id=workspace.id, name="Dup Campaign")
        session.flush()
        entry = MetricEntryRepository(session).create(
            campaign=campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), channel="email",
            source=MetricSource.MANUAL, client_request_id=uuid.uuid4().hex,
        )
        first = MetricValue(metric_entry_id=entry.id, metric_name="clicks", value=Decimal("1"))
        second = MetricValue(metric_entry_id=entry.id, metric_name="clicks", value=Decimal("2"))
        session.add_all([first, second])
        session.commit()
        return {
            "value_ids": [first.id, second.id], "entry_id": entry.id, "campaign_id": campaign.id,
            "workspace_id": workspace.id, "organization_id": organization.id,
        }


def _remove_seeded(engine, seeded: dict) -> None:
    with engine.begin() as connection:
        connection.execute(text("delete from metric_values where metric_entry_id = :e"), {"e": seeded["entry_id"]})
        connection.execute(text("delete from metric_entries where id = :e"), {"e": seeded["entry_id"]})
        connection.execute(text("delete from run_stage_executions where campaign_run_id in (select id from campaign_runs where campaign_id = :c)"), {"c": seeded["campaign_id"]})
        connection.execute(text("delete from campaign_runs where campaign_id = :c"), {"c": seeded["campaign_id"]})
        connection.execute(text("delete from campaigns where id = :c"), {"c": seeded["campaign_id"]})
        connection.execute(text("delete from workspaces where id = :w"), {"w": seeded["workspace_id"]})
        connection.execute(text("delete from organizations where id = :o"), {"o": seeded["organization_id"]})


def test_the_upgrade_fails_loudly_and_changes_nothing_when_metric_values_hold_duplicates(monkeypatch):
    engine = _migration_engine(monkeypatch)
    config = Config("alembic.ini")
    seeded = None
    try:
        if TABLE in inspect(engine).get_table_names():
            command.downgrade(config, PREDECESSOR)
        command.upgrade(config, PREDECESSOR)
        seeded = _seed_duplicate_metric_values(engine)

        with pytest.raises(RuntimeError) as excinfo:
            command.upgrade(config, REVISION)
        message = str(excinfo.value)
        assert "duplicate" in message and "metric_values" in message
        assert "never merges, deletes or repairs" in message  # loud, explicit, non-repairing

        inspector = inspect(engine)
        assert TABLE not in inspector.get_table_names()  # nothing was created...
        assert "experiment_evidence_claim_id" not in {c["name"] for c in inspector.get_columns("audit_events")}
        for table, (name, _columns) in NEW_UNIQUES.items():
            assert name not in _unique_names(engine, table)  # ...and none of the four keys was added
        with engine.connect() as connection:
            assert connection.scalar(text("select version_num from alembic_version")) == PREDECESSOR
            # No merge, no delete, no repair, no silent selection: both duplicate rows are still there, unchanged.
            rows = connection.execute(
                text("select id, value from metric_values where metric_entry_id = :e order by value"),
                {"e": seeded["entry_id"]},
            ).all()
        assert [row.id for row in rows] == [
            v for _value, v in sorted(zip((Decimal("1"), Decimal("2")), seeded["value_ids"]), key=lambda t: t[0])
        ]
        assert [row.value for row in rows] == [Decimal("1.0000"), Decimal("2.0000")]

        # Once the operator resolves the duplicates explicitly, the SAME migration succeeds.
        with engine.begin() as connection:
            connection.execute(text("delete from metric_values where id = :v"), {"v": seeded["value_ids"][1]})
        command.upgrade(config, REVISION)
        assert TABLE in inspect(engine).get_table_names()
    finally:
        try:
            if seeded is not None:
                _remove_seeded(engine, seeded)
        finally:
            engine.dispose()
            get_settings.cache_clear()
