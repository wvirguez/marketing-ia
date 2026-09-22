"""Reproducible round trip for the Pre-Execution Measurement Declaration migration
(``9d4b7e2a51c3``, down_revision ``7c1e9a4d2b68``) against the dedicated, disposable
migration database, plus model <-> DB parity against the ``create_all``
application-test database — mirrors ``tests/test_evidence_claim_migration.py``,
one migration later. Ancestor-based: asserts this revision is an ancestor of the
single head, never that it IS the head.

Also proves, on real PostgreSQL and on the MIGRATED schema (not ``create_all``):
legacy rows survive with NO backfill, the row-local CHECK backstops and the
binding-slot unique expression index behave, and the ACCEPTED DOWNGRADE HAZARD —
structured declaration data is LOST on downgrade (the downgrade is schema-reversible,
not semantically lossless).
"""
import logging
import os

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.persistence.testing import assert_safe_test_database_url

pytestmark = pytest.mark.postgres

REVISION = "9d4b7e2a51c3"
PREDECESSOR = "7c1e9a4d2b68"
CONTRACT_TABLE = "measurement_contract_versions"
SIGNAL_TABLE = "measurement_contract_signals"
SLOT = "uq_contract_signals_binding_slot"
NEW_CONTRACT_COLUMNS = {"declaration_level", "declaration_semantics_version", "baseline_window_days"}
NEW_SIGNAL_COLUMNS = {"bound_metric_name", "channel_binding", "bound_channel", "min_data_points"}
CONTRACT_CHECKS = {
    "ck_measurement_contract_versions_" + n
    for n in (
        "declaration_level_valid", "level_version_copresent", "semantics_version_v1",
        "structured_window_bounds", "baseline_iff_comparative", "baseline_window_bounds",
    )
}
SIGNAL_CHECKS = {
    "ck_measurement_contract_signals_" + n
    for n in (
        "binding_copresent", "channel_binding_valid", "bound_channel_consistent",
        "binding_text_nonblank_trimmed", "min_data_points_positive", "min_points_requires_binding",
    )
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


def _column_names(engine, table) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def _check_names(engine, table) -> set[str]:
    return {c["name"] for c in inspect(engine).get_check_constraints(table)}


def _to_predecessor(engine, config) -> None:
    if NEW_CONTRACT_COLUMNS <= _column_names(engine, CONTRACT_TABLE):
        command.downgrade(config, PREDECESSOR)
    command.upgrade(config, PREDECESSOR)


def test_migration_is_a_single_step_after_its_predecessor_and_is_an_ancestor_of_the_single_head():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_revision(REVISION).down_revision == PREDECESSOR
    heads = script.get_heads()
    assert len(heads) == 1
    assert REVISION in {revision.revision for revision in script.walk_revisions(base="base", head=heads[0])}


def test_there_is_exactly_one_declaration_migration_file():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    matching = [r for r in script.walk_revisions() if "pre-execution measurement declaration" in (r.doc or "").lower()]
    assert [r.revision for r in matching] == [REVISION]


def test_the_downgrade_hazard_is_documented_in_the_migration():
    doc = ScriptDirectory.from_config(Config("alembic.ini")).get_revision(REVISION).module.__doc__ or ""
    assert "DOWNGRADE HAZARD" in doc and "LOST" in doc


def test_declaration_migration_round_trip(monkeypatch, postgres_engine):
    engine = _migration_engine(monkeypatch)
    config = Config("alembic.ini")
    try:
        _to_predecessor(engine, config)
        assert not NEW_CONTRACT_COLUMNS & _column_names(engine, CONTRACT_TABLE)
        assert not NEW_SIGNAL_COLUMNS & _column_names(engine, SIGNAL_TABLE)
        assert SLOT not in {i["name"] for i in inspect(engine).get_indexes(SIGNAL_TABLE)}
        assert not (CONTRACT_CHECKS & _check_names(engine, CONTRACT_TABLE))
        assert not (SIGNAL_CHECKS & _check_names(engine, SIGNAL_TABLE))
        with engine.connect() as connection:
            assert connection.scalar(text("select version_num from alembic_version")) == PREDECESSOR
            contracts_before = connection.scalar(text(f"select count(*) from {CONTRACT_TABLE}"))
            signals_before = connection.scalar(text(f"select count(*) from {SIGNAL_TABLE}"))

        for cycle in range(2):
            command.upgrade(config, REVISION)
            inspector = inspect(engine)

            columns = {c["name"]: c for c in inspector.get_columns(CONTRACT_TABLE)}
            assert NEW_CONTRACT_COLUMNS <= set(columns) and all(columns[n]["nullable"] for n in NEW_CONTRACT_COLUMNS)
            signal_columns = {c["name"]: c for c in inspector.get_columns(SIGNAL_TABLE)}
            assert NEW_SIGNAL_COLUMNS <= set(signal_columns) and all(signal_columns[n]["nullable"] for n in NEW_SIGNAL_COLUMNS)
            assert CONTRACT_CHECKS <= _check_names(engine, CONTRACT_TABLE)
            assert SIGNAL_CHECKS <= _check_names(engine, SIGNAL_TABLE)
            assert SLOT in {i["name"] for i in inspector.get_indexes(SIGNAL_TABLE)}
            assert all(len(name) <= 63 for name in _constraint_definitions(engine, SIGNAL_TABLE))
            assert all(len(name) <= 63 for name in _constraint_definitions(engine, CONTRACT_TABLE))

            # No new table, no new key, no second pin: only the two existing tables changed.
            assert "measurement_declarations" not in inspector.get_table_names()
            assert "declaration_version_id" not in _column_names(engine, "execution_authorizations")

            with engine.connect() as connection:
                assert connection.scalar(text(f"select count(*) from {CONTRACT_TABLE}")) == contracts_before
                assert connection.scalar(text(f"select count(*) from {SIGNAL_TABLE}")) == signals_before
                assert connection.scalar(text("select version_num from alembic_version")) == REVISION

            # Model <-> DB parity (constraints and indexes) against the create_all application-test DB.
            for table in (CONTRACT_TABLE, SIGNAL_TABLE):
                assert _constraint_definitions(engine, table) == _constraint_definitions(postgres_engine, table), table
                assert _index_definitions(engine, table) == _index_definitions(postgres_engine, table), table

            if cycle == 0:
                command.downgrade(config, PREDECESSOR)
                assert not NEW_CONTRACT_COLUMNS & _column_names(engine, CONTRACT_TABLE)
                assert not NEW_SIGNAL_COLUMNS & _column_names(engine, SIGNAL_TABLE)
                assert SLOT not in {i["name"] for i in inspect(engine).get_indexes(SIGNAL_TABLE)}
                assert not (CONTRACT_CHECKS & _check_names(engine, CONTRACT_TABLE))
                assert not (SIGNAL_CHECKS & _check_names(engine, SIGNAL_TABLE))
                with engine.connect() as connection:
                    assert connection.scalar(text("select version_num from alembic_version")) == PREDECESSOR
    finally:
        engine.dispose()
        get_settings.cache_clear()


def _seed_legacy_and_structured(engine):
    """At REVISION: one Experiment holding a LEGACY Contract and another holding a STRUCTURED one,
    written through the real production services. Returns their internal ids."""
    from app.strategy.models import MeasurementContractRequiredSignal, MeasurementContractVersion
    from tests.declarationtest import bound_signal, declare, legacy_signal
    from tests.strategytest import build_current_experiment
    from tests.test_execution_authorization_domain import _define

    with Session(engine) as session:
        campaign, _s, _h, legacy_experiment, actor = build_current_experiment(session, campaign_name="Migration Legacy")
        legacy_definition = _define(session, campaign, legacy_experiment, actor, key="d-legacy")
        legacy, _signals, _d, _c = declare(
            session, campaign, legacy_experiment, actor, legacy_definition, level=None, window=None,
            signals=[legacy_signal()], key="c-legacy",
        )
        campaign2, _s2, _h2, structured_experiment, actor2 = build_current_experiment(session, campaign_name="Migration Structured")
        definition = _define(session, campaign2, structured_experiment, actor2, key="d-structured")
        structured, _signals2, _d2, _c2 = declare(
            session, campaign2, structured_experiment, actor2, definition, level="DESCRIPTIVE", window=14,
            signals=[bound_signal(binding="EXACT", channel="email", min_points=2)], key="c-structured",
        )
        assert session.scalar(
            select(MeasurementContractRequiredSignal.bound_metric_name).where(
                MeasurementContractRequiredSignal.contract_version_id == structured.id
            )
        ) == "clicks"
        assert session.scalar(select(MeasurementContractVersion.declaration_level).where(MeasurementContractVersion.id == structured.id)) == "DESCRIPTIVE"
        return {"legacy": legacy.id, "structured": structured.id}


def _wipe_seeded(engine) -> None:
    """The migration database is disposable and holds no rows other tests rely on: remove every row the seeding wrote."""
    with engine.begin() as connection:
        connection.execute(text("truncate table organizations, users cascade"))


def _contract_rows(engine, ids) -> dict:
    with engine.connect() as connection:
        return {
            key: connection.execute(
                text("select measurement_window_days from measurement_contract_versions where id = :i"), {"i": value}
            ).scalar_one_or_none()
            for key, value in ids.items()
        }


def test_legacy_rows_are_preserved_without_backfill_and_downgrade_loses_structured_data(monkeypatch):
    engine = _migration_engine(monkeypatch)
    config = Config("alembic.ini")
    try:
        _to_predecessor(engine, config)
        command.upgrade(config, REVISION)
        ids = _seed_legacy_and_structured(engine)

        with engine.connect() as connection:
            structured = connection.execute(
                text("select declaration_level, declaration_semantics_version, baseline_window_days from measurement_contract_versions where id = :i"),
                {"i": ids["structured"]},
            ).one()
            assert tuple(structured) == ("DESCRIPTIVE", 1, None)
            legacy = connection.execute(
                text("select declaration_level, declaration_semantics_version, baseline_window_days from measurement_contract_versions where id = :i"),
                {"i": ids["legacy"]},
            ).one()
            assert tuple(legacy) == (None, None, None)  # a legacy write leaves every new column NULL

        # DOWNGRADE HAZARD: the rows survive, the structured declaration does not.
        command.downgrade(config, PREDECESSOR)
        assert not NEW_CONTRACT_COLUMNS & _column_names(engine, CONTRACT_TABLE)
        assert _contract_rows(engine, ids)["structured"] == 14  # the Contract row itself is intact...

        # Upgrading again adds nullable columns and backfills NOTHING: the previously structured
        # Contract is now indistinguishable from a legacy one, i.e. its declaration was LOST.
        command.upgrade(config, REVISION)
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "select id, declaration_level, declaration_semantics_version, baseline_window_days "
                    "from measurement_contract_versions where id in (:a, :b)"
                ),
                {"a": ids["legacy"], "b": ids["structured"]},
            ).all()
            assert {row.id: tuple(row[1:]) for row in rows} == {ids["legacy"]: (None, None, None), ids["structured"]: (None, None, None)}
            bindings = connection.execute(
                text("select count(*) from measurement_contract_signals where bound_metric_name is not null")
            ).scalar_one()
            assert bindings == 0
    finally:
        _wipe_seeded(engine)
        engine.dispose()
        get_settings.cache_clear()


def test_backstops_and_the_slot_index_behave_on_the_migrated_schema(monkeypatch):
    engine = _migration_engine(monkeypatch)
    config = Config("alembic.ini")
    try:
        _to_predecessor(engine, config)
        command.upgrade(config, REVISION)
        ids = _seed_legacy_and_structured(engine)

        def try_insert(sql: str, params: dict) -> str | None:
            with engine.connect() as connection:
                try:
                    connection.execute(text(sql), params)
                    connection.rollback()
                    return None
                except IntegrityError as exc:
                    connection.rollback()
                    return exc.orig.diag.constraint_name

        with engine.connect() as connection:
            contract = connection.execute(
                text("select workspace_id, experiment_id, id from measurement_contract_versions where id = :i"),
                {"i": ids["structured"]},
            ).one()
        insert_signal = (
            "insert into measurement_contract_signals (id, public_id, workspace_id, experiment_id, contract_version_id, "
            "ordinal, name, description, bound_metric_name, channel_binding, bound_channel) values "
            "(gen_random_uuid(), :pid, :w, :e, :c, :ordinal, :name, 'd', :metric, :binding, :channel)"
        )
        base = {"w": contract.workspace_id, "e": contract.experiment_id, "c": contract.id}

        # The seeded signal is EXACT(clicks, email): the same slot again is blocked, another channel is allowed.
        assert try_insert(insert_signal, {**base, "pid": "RSG-DUPLICATE01", "ordinal": 2, "name": "dup", "metric": "clicks", "binding": "EXACT", "channel": "email"}) == SLOT
        assert try_insert(insert_signal, {**base, "pid": "RSG-OTHERCHAN01", "ordinal": 2, "name": "other", "metric": "clicks", "binding": "EXACT", "channel": "sms"}) is None
        # Row-local CHECKs are live on the migrated schema.
        assert try_insert(insert_signal, {**base, "pid": "RSG-BADCHANNEL1", "ordinal": 3, "name": "bad", "metric": "clicks", "binding": "ANY", "channel": "email"}) == (
            "ck_measurement_contract_signals_bound_channel_consistent"
        )
        assert try_insert(insert_signal, {**base, "pid": "RSG-BADBINDING1", "ordinal": 3, "name": "bad2", "metric": "clicks", "binding": "SOME", "channel": None}) == (
            "ck_measurement_contract_signals_channel_binding_valid"
        )
        # Legacy all-NULL signals never collide with each other.
        with engine.connect() as connection:
            legacy = connection.execute(
                text("select workspace_id, experiment_id, id from measurement_contract_versions where id = :i"),
                {"i": ids["legacy"]},
            ).one()
        legacy_insert = (
            "insert into measurement_contract_signals (id, public_id, workspace_id, experiment_id, contract_version_id, "
            "ordinal, name, description) values (gen_random_uuid(), :pid, :w, :e, :c, :ordinal, :name, 'd')"
        )
        with engine.begin() as connection:
            for ordinal in (2, 3, 4):
                connection.execute(
                    text(legacy_insert),
                    {"pid": f"RSG-LEGACYNULL{ordinal}", "w": legacy.workspace_id, "e": legacy.experiment_id, "c": legacy.id, "ordinal": ordinal, "name": f"legacy-{ordinal}"},
                )
        with engine.connect() as connection:
            assert connection.scalar(
                text("select count(*) from measurement_contract_signals where contract_version_id = :c"), {"c": legacy.id}
            ) == 4
        # Contract-level CHECKs on the migrated schema.
        with engine.connect() as connection:
            try:
                connection.execute(
                    text("update measurement_contract_versions set measurement_window_days = 3 where id = :i"),
                    {"i": ids["structured"]},
                )
                raise AssertionError("expected the structured window bound to be enforced")
            except IntegrityError as exc:
                assert exc.orig.diag.constraint_name == "ck_measurement_contract_versions_structured_window_bounds"
    finally:
        _wipe_seeded(engine)
        engine.dispose()
        get_settings.cache_clear()
