"""Structural database backstops for MeasurementContractVersion and
MeasurementContractRequiredSignal (MVP-39, frozen MVP-39A/-39B): every test
here BYPASSES the service and inserts directly, proving the database itself
— not application code — rejects the violation. All marked `postgres`.

Honest limit, documented rather than hidden: at least one RequiredSignal per
Contract version is a SERVICE/SCHEMA-level invariant only (MVP39B-OBS-1) —
no PostgreSQL CHECK can prove a cross-row minimum, the same class of gap as
MVP37B-OBS-1/MVP38A-OBS-1.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from sqlalchemy import func, select

from app.audit.models import ActorType, AuditEvent
from app.core.ids import generate_public_id
from app.strategy.definition_service import ExperimentDefinitionService
from app.strategy.models import ExperimentDefinitionVersion, MeasurementContractRequiredSignal, MeasurementContractVersion
from app.strategy.repository import MeasurementContractRepository
from tests.strategytest import build_current_experiment
from tests.test_experiment_definition_domain import fields

pytestmark = pytest.mark.postgres


def _version(session, campaign, experiment, actor, *, base_version=0, key="v-1", **overrides):
    version, _created = ExperimentDefinitionService(session).write_version(
        campaign=campaign, experiment_public_id=experiment.public_id, base_version=base_version,
        client_request_id=key, fields=fields(**overrides), actor_user_id=actor.id,
    )
    return version


def _contract_row(definition_version, **overrides) -> MeasurementContractVersion:
    values = dict(
        public_id=generate_public_id("MSC"), workspace_id=definition_version.workspace_id,
        experiment_id=definition_version.experiment_id, definition_version_id=definition_version.id,
        version=1, client_request_id=uuid.uuid4().hex,
    )
    values.update(overrides)
    return MeasurementContractVersion(**values)


def _signal_row(contract: MeasurementContractVersion, **overrides) -> MeasurementContractRequiredSignal:
    values = dict(
        public_id=generate_public_id("RSG"), workspace_id=contract.workspace_id, experiment_id=contract.experiment_id,
        contract_version_id=contract.id, ordinal=1, name="CTR", description="d",
    )
    values.update(overrides)
    return MeasurementContractRequiredSignal(**values)


def _violation(session, row) -> str:
    with pytest.raises(IntegrityError) as excinfo:
        with session.begin_nested():
            session.add(row)
            session.flush()
    return excinfo.value.orig.diag.constraint_name


def test_duplicate_experiment_version_is_rejected_by_the_database(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _version(db_session, campaign, experiment, actor)
    db_session.add(_contract_row(version, version=1))
    db_session.flush()
    assert _violation(db_session, _contract_row(version, version=1)) == "uq_measurement_contract_versions_experiment_version"


def test_duplicate_workspace_client_request_id_is_rejected_by_the_database(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _version(db_session, campaign, experiment, actor)
    db_session.add(_contract_row(version, version=1, client_request_id="shared"))
    db_session.flush()
    assert _violation(db_session, _contract_row(version, version=2, client_request_id="shared")) == (
        "uq_measurement_contract_versions_workspace_client_request_id"
    )


def test_composite_fk_rejects_an_experiment_that_does_not_own_the_pinned_version(db_session) -> None:
    c1, _s1, _h1, first, a1 = build_current_experiment(db_session, campaign_name="Tenant One")
    _c2, _s2, _h2, second, _a2 = build_current_experiment(db_session, campaign_name="Tenant Two")
    version = _version(db_session, c1, first, a1)
    assert _violation(db_session, _contract_row(version, experiment_id=second.id)) == (
        "fk_measurement_contract_versions_definition_version_workspace"
    )


def test_composite_fk_rejects_a_workspace_that_does_not_own_the_pinned_version(db_session) -> None:
    c1, _s1, _h1, first, a1 = build_current_experiment(db_session, campaign_name="Tenant One")
    _c2, _s2, _h2, second, _a2 = build_current_experiment(db_session, campaign_name="Tenant Two")
    version = _version(db_session, c1, first, a1)
    assert _violation(db_session, _contract_row(version, workspace_id=second.workspace_id)) == (
        "fk_measurement_contract_versions_definition_version_workspace"
    )


def test_composite_fk_rejects_a_version_that_does_not_exist(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _version(db_session, campaign, experiment, actor)
    assert _violation(db_session, _contract_row(version, definition_version_id=uuid.uuid4())) == (
        "fk_measurement_contract_versions_definition_version_workspace"
    )


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"version": 0}, "ck_measurement_contract_versions_version_positive"),
        ({"version": -1}, "ck_measurement_contract_versions_version_positive"),
        ({"measurement_window_days": 0}, "ck_measurement_contract_versions_window_days_positive"),
        ({"measurement_window_days": -3}, "ck_measurement_contract_versions_window_days_positive"),
        ({"minimum_evidence": "   "}, "ck_measurement_contract_versions_optional_text_nonblank"),
        ({"success_criterion": ""}, "ck_measurement_contract_versions_optional_text_nonblank"),
    ],
)
def test_database_check_constraints_reject_invalid_contract_rows(db_session, overrides, expected) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _version(db_session, campaign, experiment, actor)
    assert _violation(db_session, _contract_row(version, **overrides)) == expected


def test_duplicate_signal_ordinal_is_rejected_by_the_database(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _version(db_session, campaign, experiment, actor)
    contract = _contract_row(version)
    db_session.add(contract)
    db_session.flush()
    db_session.add(_signal_row(contract, ordinal=1, name="A"))
    db_session.flush()
    assert _violation(db_session, _signal_row(contract, ordinal=1, name="B")) == (
        "uq_measurement_contract_signals_contract_version_ordinal"
    )


def test_duplicate_signal_exact_name_is_rejected_by_the_database(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _version(db_session, campaign, experiment, actor)
    contract = _contract_row(version)
    db_session.add(contract)
    db_session.flush()
    db_session.add(_signal_row(contract, ordinal=1, name="CTR"))
    db_session.flush()
    assert _violation(db_session, _signal_row(contract, ordinal=2, name="CTR")) == (
        "uq_measurement_contract_signals_contract_version_name"
    )


def test_composite_fk_rejects_a_signal_whose_experiment_does_not_match_its_contract(db_session) -> None:
    c1, _s1, _h1, first, a1 = build_current_experiment(db_session, campaign_name="Tenant One")
    _c2, _s2, _h2, second, _a2 = build_current_experiment(db_session, campaign_name="Tenant Two")
    version = _version(db_session, c1, first, a1)
    contract = _contract_row(version)
    db_session.add(contract)
    db_session.flush()
    assert _violation(db_session, _signal_row(contract, experiment_id=second.id)) == (
        "fk_measurement_contract_signals_contract_version_workspace"
    )


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"ordinal": 0}, "ck_measurement_contract_signals_ordinal_positive"),
        ({"ordinal": -1}, "ck_measurement_contract_signals_ordinal_positive"),
        ({"name": ""}, "ck_measurement_contract_signals_text_fields_nonblank"),
        ({"name": "   "}, "ck_measurement_contract_signals_text_fields_nonblank"),
        ({"description": ""}, "ck_measurement_contract_signals_text_fields_nonblank"),
        ({"expected_direction": "SIDEWAYS"}, "ck_measurement_contract_signals_expected_direction_valid"),
        ({"evidence_requirement": "   "}, "ck_measurement_contract_signals_evidence_nonblank_if_present"),
    ],
)
def test_database_check_constraints_reject_invalid_signal_rows(db_session, overrides, expected) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _version(db_session, campaign, experiment, actor)
    contract = _contract_row(version)
    db_session.add(contract)
    db_session.flush()
    assert _violation(db_session, _signal_row(contract, **overrides)) == expected


def test_expected_direction_accepts_every_frozen_value(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _version(db_session, campaign, experiment, actor)
    contract = _contract_row(version)
    db_session.add(contract)
    db_session.flush()
    for ordinal, direction in enumerate(("INCREASE", "DECREASE", "TARGET", "NO_DIRECTION", None), start=1):
        db_session.add(_signal_row(contract, ordinal=ordinal, name=f"Signal {ordinal}", expected_direction=direction))
    db_session.flush()  # accepted by the database


def test_database_does_not_know_which_version_is_the_tip(db_session) -> None:
    """Documents the honest limit: the Definition-tip-only rule is
    SERVICE-level (mirrors test_experiment_variant_db.py's own identical
    documentation for Variant, MVP38A-OBS-2)."""
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    v1 = _version(db_session, campaign, experiment, actor)
    _version(db_session, campaign, experiment, actor, base_version=1, key="v-2", changed_factor="Second")
    db_session.add(_contract_row(v1))
    db_session.flush()  # accepted by the database


def test_at_least_one_signal_is_not_database_enforced(db_session) -> None:
    """MVP39B-OBS-1: a Contract version with ZERO signals is structurally
    possible at the database layer — the database cannot prove a cross-row
    minimum. The schema (Pydantic ``min_length=1``) is the actual backstop,
    proven separately in ``test_measurement_contract_api.py``."""
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _version(db_session, campaign, experiment, actor)
    db_session.add(_contract_row(version))
    db_session.flush()  # accepted by the database, zero signals


def test_columns_are_exactly_the_frozen_set_and_identifiers_fit_postgresql() -> None:
    for table, forbidden in (
        (
            MeasurementContractVersion.__table__,
            {"status", "frozen_at", "execution_authorized", "winner", "result", "observed_value", "created_by", "updated_at"},
        ),
        (
            MeasurementContractRequiredSignal.__table__,
            {"observed_value", "score", "threshold", "operator", "metric_entry_id", "created_by", "updated_at"},
        ),
    ):
        for fk in table.foreign_key_constraints:
            assert fk.ondelete is None and fk.onupdate is None
            assert len(fk.name) <= 63
        names = [c.name for c in table.constraints if c.name] + [i.name for i in table.indexes]
        assert all(len(str(name)) <= 63 for name in names), [n for n in names if len(str(n)) > 63]
        assert not forbidden & set(table.columns.keys())
    assert set(MeasurementContractVersion.__table__.columns.keys()) == {
        "id", "public_id", "workspace_id", "experiment_id", "definition_version_id", "version",
        "measurement_window_days", "minimum_evidence", "success_criterion", "analysis_method_intent",
        "stopping_rule", "decision_rule_intent", "client_request_id", "created_at",
    }
    assert set(MeasurementContractRequiredSignal.__table__.columns.keys()) == {
        "id", "public_id", "workspace_id", "experiment_id", "contract_version_id", "ordinal", "name", "description",
        "expected_direction", "evidence_requirement", "tracking_required", "created_at",
    }
    assert "fk_measurement_contract_versions_definition_version_workspace" in {
        c.name for c in MeasurementContractVersion.__table__.constraints
    }
    assert "fk_measurement_contract_signals_contract_version_workspace" in {
        c.name for c in MeasurementContractRequiredSignal.__table__.constraints
    }


def test_no_candidate_key_added_to_experiment_definition_versions_this_migration() -> None:
    """MVP-39B §3: the composite FK reuses the EXISTING MVP-38 candidate
    key — no new candidate key was added to ExperimentDefinitionVersion."""
    names = {c.name for c in ExperimentDefinitionVersion.__table__.constraints if c.name}
    assert "uq_experiment_definition_versions_id_experiment_workspace" in names
    assert not any(name.startswith("uq_experiment_definition_versions_") and "measurement" in name for name in names)


def test_audit_link_column_is_a_nullable_fk_to_the_contract_table(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    event = AuditEvent(
        public_id=generate_public_id("AUDT"), workspace_id=campaign.workspace_id,
        event_type="strategy.measurement_contract.declared", actor_type=ActorType.USER, actor_user_id=actor.id,
        measurement_contract_id=uuid.uuid4(),
    )
    assert _violation(db_session, event) == "fk_audit_events_measurement_contract_id"
    ok = AuditEvent(
        public_id=generate_public_id("AUDT"), workspace_id=campaign.workspace_id,
        event_type="unrelated.event", actor_type=ActorType.SYSTEM,
    )
    db_session.add(ok)
    db_session.flush()
    assert ok.measurement_contract_id is None


def test_atomicity_a_failing_signal_rolls_back_the_already_flushed_contract_row_too(db_session) -> None:
    """Real, non-mocked proof (MVP-39C §35/§61): ``MeasurementContractRepository.
    create()`` builds the Contract row and every RequiredSignal row as one
    aggregate unit — the Contract row is flushed first (to obtain its id for
    the signals' FK), but a later signal violating a DB CHECK must roll back
    that already-flushed Contract row too, leaving zero partial rows of
    either table. This exercises the real repository code path, bypassing
    only the schema/service layers that would normally prevent a blank name
    from ever reaching it."""
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _version(db_session, campaign, experiment, actor)
    repo = MeasurementContractRepository(db_session)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            repo.create(
                experiment_id=version.experiment_id, workspace_id=version.workspace_id,
                definition_version_id=version.id, version=1,
                measurement_window_days=None, minimum_evidence=None, success_criterion=None,
                analysis_method_intent=None, stopping_rule=None, decision_rule_intent=None,
                client_request_id=uuid.uuid4().hex,
                signals=[{"name": "OK", "description": "d"}, {"name": "   ", "description": "d2"}],
            )
    contract_count = db_session.scalar(
        select(func.count()).select_from(MeasurementContractVersion).where(
            MeasurementContractVersion.experiment_id == version.experiment_id
        )
    )
    signal_count = db_session.scalar(
        select(func.count()).select_from(MeasurementContractRequiredSignal).where(
            MeasurementContractRequiredSignal.experiment_id == version.experiment_id
        )
    )
    assert contract_count == 0 and signal_count == 0  # nothing partial persisted


def test_signal_and_contract_list_read_methods(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _version(db_session, campaign, experiment, actor)
    contract = _contract_row(version)
    db_session.add(contract)
    db_session.flush()
    db_session.add(_signal_row(contract, ordinal=2, name="Second"))
    db_session.add(_signal_row(contract, ordinal=1, name="First"))
    db_session.flush()
    repo = MeasurementContractRepository(db_session)
    signals = repo.list_signals_for_version(contract_version_id=contract.id)
    assert [s.name for s in signals] == ["First", "Second"]  # ordered by ordinal, never insertion order
    assert repo.signal_names_for_version(contract_version_id=contract.id) == ["First", "Second"] or set(
        repo.signal_names_for_version(contract_version_id=contract.id)
    ) == {"First", "Second"}
    assert repo.exists_for_definition_version(definition_version_id=version.id) is True
    states = repo.contract_states_for_versions(definition_version_ids=[version.id], workspace_id=campaign.workspace_id)
    assert states == {version.id: (True, 1)}
