"""Service-level tests for Governed Measurement Contract (MVP-39, frozen
MVP-39A/-39B): the frozen write order, canonical lock order, the exact
IntegrityError translation contract, the central Definition-lock seam
shared with Variant, Contract/Variant independence, and the append-only
writer surface. All marked `postgres`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit.models import AuditEvent
from app.core.api_errors import (
    ExperimentDefinitionPinnedError,
    ForbiddenError,
    IdempotencyKeyConflictError,
    MeasurementContractBaseStaleError,
    MeasurementContractDefinitionVersionNotCurrentError,
    MeasurementContractStrategyStaleError,
    MeasurementContractSuccessCriterionRequiredError,
    MeasurementContractUnchangedError,
)
from app.strategy.definition_service import ExperimentDefinitionService
from app.strategy.measurement_contract_service import ExperimentMeasurementContractService
from app.strategy.repository import ExperimentRepository, MeasurementContractRepository, StrategyRepository
from app.strategy.variant_service import ExperimentVariantService
from tests.strategytest import build_current_experiment
from tests.test_experiment_definition_domain import _integrity_error, fields

pytestmark = pytest.mark.postgres

_UQ_EXPERIMENT_VERSION = "uq_measurement_contract_versions_experiment_version"
_UQ_CLIENT_REQUEST_ID = "uq_measurement_contract_versions_workspace_client_request_id"


def _signal(**overrides: object) -> dict:
    payload = {
        "name": "Click-through rate",
        "description": "The share of viewers who clicked through.",
        "expected_direction": None,
        "evidence_requirement": None,
        "tracking_required": False,
    }
    payload.update(overrides)
    return payload


def _define(session, campaign, experiment, actor, *, base_version=0, key="d-1", **overrides):
    return ExperimentDefinitionService(session).write_version(
        campaign=campaign, experiment_public_id=experiment.public_id, base_version=base_version,
        client_request_id=key, fields=fields(**overrides), actor_user_id=actor.id,
    )[0]


def _declare(session, campaign, experiment, actor, version, *, base_version=0, key="c-1", signals=None, **overrides):
    contract_fields = {
        "measurement_window_days": None, "minimum_evidence": None, "success_criterion": None,
        "analysis_method_intent": None, "stopping_rule": None, "decision_rule_intent": None,
    }
    contract_fields.update(overrides)
    return ExperimentMeasurementContractService(session).declare_or_revise(
        campaign=campaign, experiment_public_id=experiment.public_id, base_version=base_version,
        client_request_id=key, definition_version_public_id=version.public_id,
        signals=signals if signals is not None else [_signal()],
        actor_user_id=actor.id, **contract_fields,
    )


def _declare_variant(session, campaign, experiment, actor, version, *, label="A", key="v-1"):
    return ExperimentVariantService(session).declare_variant(
        campaign=campaign, experiment_public_id=experiment.public_id, definition_version_public_id=version.public_id,
        label=label, condition_description="d", client_request_id=key, actor_user_id=actor.id,
    )


def test_declaration_pins_definition_and_appends_sequential_versions(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)
    first, _signals, pinned, created = _declare(db_session, campaign, experiment, actor, version, key="c-1")
    assert created is True and pinned.id == version.id
    assert first.version == 1 and first.public_id.startswith("MSC-")
    with pytest.raises(ExperimentDefinitionPinnedError):
        _define(db_session, campaign, experiment, actor, base_version=1, key="d-2", changed_factor="Blocked")
    second, _signals2, _pinned, created = _declare(
        db_session, campaign, experiment, actor, version, base_version=1, key="c-2",
        signals=[_signal(name="Different")],
    )
    assert created is True and second.version == 2


def test_contract_and_variant_are_independent_siblings_under_one_pin(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)
    _declare_variant(db_session, campaign, experiment, actor, version, key="v-1")
    # A Contract may still be declared after a Variant already pinned the Definition.
    contract, _signals, _pinned, created = _declare(db_session, campaign, experiment, actor, version, key="c-1")
    assert created is True

    campaign2, _s2, _h2, experiment2, actor2 = build_current_experiment(db_session, campaign_name="Contract First")
    version2 = _define(db_session, campaign2, experiment2, actor2)
    _declare(db_session, campaign2, experiment2, actor2, version2, key="c-1")
    # A Variant may still be declared after a Contract already pinned the Definition.
    variant, _pinned2, created2 = _declare_variant(db_session, campaign2, experiment2, actor2, version2, key="v-1")
    assert created2 is True


def test_a_version_of_another_experiment_or_no_definition_is_a_non_leaky_forbidden(db_session) -> None:
    campaign, _s, hypothesis, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)
    from app.strategy.service import StrategyService

    other = StrategyService(db_session).create_experiment(
        campaign=campaign, hypothesis_public_id=hypothesis.public_id, description="other", actor_user_id=actor.id
    )
    with pytest.raises(ForbiddenError):
        _declare(db_session, campaign, other, actor, version)  # the EXD belongs to the first Experiment


def test_stale_definition_pin_and_material_conflict_errors(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    v1 = _define(db_session, campaign, experiment, actor)
    v2 = _define(db_session, campaign, experiment, actor, base_version=1, key="d-2", changed_factor="Second")
    with pytest.raises(MeasurementContractDefinitionVersionNotCurrentError):
        _declare(db_session, campaign, experiment, actor, v1, key="c-1")
    _declare(db_session, campaign, experiment, actor, v2, key="c-1")
    with pytest.raises(IdempotencyKeyConflictError):
        _declare(db_session, campaign, experiment, actor, v2, key="c-1", signals=[_signal(name="Different")])


def test_base_stale_and_unchanged_revision(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)
    _declare(db_session, campaign, experiment, actor, version, base_version=0, key="c-1")
    with pytest.raises(MeasurementContractBaseStaleError):
        _declare(db_session, campaign, experiment, actor, version, base_version=0, key="c-2")
    with pytest.raises(MeasurementContractUnchangedError):
        _declare(db_session, campaign, experiment, actor, version, base_version=1, key="c-3")


def test_controlled_comparison_requires_a_success_criterion(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor, comparison_type="CONTROLLED", controlled_factors=["Format"])
    with pytest.raises(MeasurementContractSuccessCriterionRequiredError):
        _declare(db_session, campaign, experiment, actor, version, key="c-1")
    row, _signals, _pinned, created = _declare(
        db_session, campaign, experiment, actor, version, key="c-2", success_criterion="CTR improves."
    )
    assert created is True

    campaign2, _s2, _h2, experiment2, actor2 = build_current_experiment(db_session, campaign_name="Observational Contract")
    version2 = _define(db_session, campaign2, experiment2, actor2)  # OBSERVATIONAL
    row2, _signals2, _pinned2, created2 = _declare(db_session, campaign2, experiment2, actor2, version2, key="c-1")
    assert created2 is True and row2.success_criterion is None


def test_locks_are_taken_strategy_row_then_experiment_row_and_a_replay_takes_none(db_session, monkeypatch) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)
    order: list[str] = []
    real_strategy_get = StrategyRepository.get_by_id
    real_experiment_get = ExperimentRepository.get_by_id

    def strategy_get(self, strategy_id, *, for_update=False):
        if for_update:
            order.append("strategy")
        return real_strategy_get(self, strategy_id, for_update=for_update)

    def experiment_get(self, experiment_id, *, for_update=False):
        if for_update:
            order.append("experiment")
        return real_experiment_get(self, experiment_id, for_update=for_update)

    monkeypatch.setattr(StrategyRepository, "get_by_id", strategy_get)
    monkeypatch.setattr(ExperimentRepository, "get_by_id", experiment_get)

    _declare(db_session, campaign, experiment, actor, version, key="c-1")
    assert order == ["strategy", "experiment"]
    order.clear()
    _row, _signals, _pinned, created = _declare(db_session, campaign, experiment, actor, version, key="c-1")  # matching replay
    assert created is False and order == []


def test_only_the_two_known_unique_violations_are_translated(db_session, monkeypatch) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)

    def boom(constraint):
        def create(self, **kwargs):
            raise _integrity_error(constraint)

        return create

    monkeypatch.setattr(MeasurementContractRepository, "create", boom(_UQ_EXPERIMENT_VERSION))
    with pytest.raises(MeasurementContractBaseStaleError):
        _declare(db_session, campaign, experiment, actor, version, key="ka")

    for other in (
        "uq_measurement_contract_signals_contract_version_name", "ck_measurement_contract_versions_version_positive",
        "uq_something_else", None,
    ):
        monkeypatch.setattr(MeasurementContractRepository, "create", boom(other))
        with pytest.raises(IntegrityError):
            _declare(db_session, campaign, experiment, actor, version, key=f"kb-{other}")

    monkeypatch.setattr(MeasurementContractRepository, "create", boom(_UQ_CLIENT_REQUEST_ID))
    with pytest.raises(IntegrityError):
        _declare(db_session, campaign, experiment, actor, version, key="kc")


def test_the_definition_lock_is_decided_only_by_the_central_seam(db_session, monkeypatch) -> None:
    """A Contract-only pin (no Variant present) must ALSO block Definition
    revision — proving the seam consults Contract existence, not merely
    Variant existence."""
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)
    _declare(db_session, campaign, experiment, actor, version, key="c-1")
    with pytest.raises(ExperimentDefinitionPinnedError):
        _define(db_session, campaign, experiment, actor, base_version=1, key="d-2", changed_factor="Blocked")


def test_the_pin_state_reports_the_contract_only_case(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)
    service = ExperimentDefinitionService(db_session)
    assert service.pin_states_for_versions(
        workspace_id=campaign.workspace_id, definition_version_ids=[version.id]
    ) == {version.id: (0, False, False, None)}
    _declare(db_session, campaign, experiment, actor, version, key="c-1")
    assert service.pin_states_for_versions(
        workspace_id=campaign.workspace_id, definition_version_ids=[version.id]
    ) == {version.id: (0, True, True, 1)}


def test_declaration_writes_exactly_one_audit_event_per_version(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)
    _declare(db_session, campaign, experiment, actor, version, key="c-1")
    events = list(
        db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "strategy.measurement_contract.declared", AuditEvent.experiment_id == experiment.id
            )
        )
    )
    assert len(events) == 1 and events[0].new_state == "v1" and events[0].previous_state is None
    assert events[0].measurement_contract_id is not None


def test_writer_surface_is_append_only_and_has_no_lifecycle() -> None:
    public = {name for name in dir(MeasurementContractRepository) if not name.startswith("_")}
    assert public == {
        "create", "get_by_workspace_and_request_id", "get_tip", "list_for_experiment", "list_signals_for_version",
        "signal_names_for_version", "exists_for_definition_version", "contract_states_for_versions",
    }
    assert not {
        n for n in dir(ExperimentMeasurementContractService) if n.startswith(("update", "delete", "edit", "correct", "retire", "freeze"))
    }
    assert hasattr(ExperimentMeasurementContractService, "declare_or_revise")


def test_strategy_stale_rejects_new_writes_but_allows_matching_replay(db_session) -> None:
    from app.strategy.service import StrategyService
    from tests.test_experiment_concurrency import _second_eligible_approval
    from app.orchestration.service import StrategyRevisionService

    campaign, strategy, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)
    _declare(db_session, campaign, experiment, actor, version, key="c-1")
    approval = _second_eligible_approval(db_session, campaign=campaign, actor_id=actor.id)
    StrategyRevisionService(db_session).revise_strategy(
        campaign=campaign, base_strategy_public_id=strategy.public_id, strategic_approval_public_id=approval.public_id,
        summary="x", positioning_statement="y", actor_user_id=actor.id,
    )
    with pytest.raises(MeasurementContractStrategyStaleError):
        _declare(db_session, campaign, experiment, actor, version, base_version=1, key="c-2", signals=[_signal(name="Different")])
    # A matching replay of the ALREADY-COMMITTED v1 still succeeds.
    _row, _signals, _pinned, created = _declare(db_session, campaign, experiment, actor, version, key="c-1")
    assert created is False
