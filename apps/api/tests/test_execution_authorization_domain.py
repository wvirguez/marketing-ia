"""Service-level tests for Governed Execution Authorization (MVP-40, frozen
Execution Authorization Discovery/Design Freeze): the frozen write order,
canonical lock order, single-active-per-Experiment auto-supersession,
cardinality/prerequisite enforcement, the Contract-freeze seam, and the
append-only writer surface. All marked `postgres`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit.models import AuditEvent
from app.core.api_errors import (
    ExecutionAuthorizationInsufficientVariantsError,
    ExecutionAuthorizationNoMeasurementContractError,
    ExecutionAuthorizationNoneActiveError,
    ExecutionAuthorizationStrategyStaleError,
    ForbiddenError,
    IdempotencyKeyConflictError,
    MeasurementContractFrozenByAuthorizationError,
)
from app.strategy.definition_service import ExperimentDefinitionService
from app.strategy.execution_authorization_service import ExperimentExecutionAuthorizationService
from app.strategy.measurement_contract_service import ExperimentMeasurementContractService
from app.strategy.repository import ExecutionAuthorizationRepository, ExperimentRepository, StrategyRepository
from app.strategy.variant_service import ExperimentVariantService
from tests.strategytest import build_current_experiment
from tests.test_experiment_definition_domain import _integrity_error, fields

pytestmark = pytest.mark.postgres

_UQ_CLIENT_REQUEST_ID = "uq_execution_authorizations_workspace_client_request_id"


def _define(session, campaign, experiment, actor, *, base_version=0, key="d-1", **overrides):
    return ExperimentDefinitionService(session).write_version(
        campaign=campaign, experiment_public_id=experiment.public_id, base_version=base_version,
        client_request_id=key, fields=fields(**overrides), actor_user_id=actor.id,
    )[0]


def _declare_variant(session, campaign, experiment, actor, version, *, label="A", key="v-1"):
    return ExperimentVariantService(session).declare_variant(
        campaign=campaign, experiment_public_id=experiment.public_id, definition_version_public_id=version.public_id,
        label=label, condition_description="d", client_request_id=key, actor_user_id=actor.id,
    )[0]


def _declare_contract(session, campaign, experiment, actor, version, *, key="c-1", base_version=0, **overrides):
    contract_fields = {
        "measurement_window_days": None, "minimum_evidence": None, "success_criterion": None,
        "analysis_method_intent": None, "stopping_rule": None, "decision_rule_intent": None,
    }
    contract_fields.update(overrides)
    signal = {
        "name": "Click-through rate", "description": "CTR.", "expected_direction": None,
        "evidence_requirement": None, "tracking_required": False,
    }
    return ExperimentMeasurementContractService(session).declare_or_revise(
        campaign=campaign, experiment_public_id=experiment.public_id, base_version=base_version,
        client_request_id=key, definition_version_public_id=version.public_id, signals=[signal],
        actor_user_id=actor.id, **contract_fields,
    )[0]


def _authorize(session, campaign, experiment, actor, *, key="a-1", unit="visitor", design="50/50 split."):
    return ExperimentExecutionAuthorizationService(session).authorize(
        campaign=campaign, experiment_public_id=experiment.public_id, client_request_id=key,
        unit_of_assignment=unit, allocation_design=design, actor_user_id=actor.id,
    )


def _ready(session, campaign, experiment, actor, *, label="A", d_key="d-1", v_key="v-1", c_key="c-1"):
    """Definition + one Variant + a Contract — the minimum OBSERVATIONAL-ready state."""
    version = _define(session, campaign, experiment, actor, key=d_key)
    _declare_variant(session, campaign, experiment, actor, version, label=label, key=v_key)
    _declare_contract(session, campaign, experiment, actor, version, key=c_key)
    return version


def test_authorization_pins_current_definition_variant_and_contract_tip(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _ready(db_session, campaign, experiment, actor)
    authorization, snapshot, created = _authorize(db_session, campaign, experiment, actor)
    assert created is True
    assert authorization.public_id.startswith("EXA-")
    assert authorization.definition_version_id == version.id
    assert authorization.revoked_at is None
    assert len(snapshot) == 1
    variant = ExperimentVariantService(db_session).variants.list_for_definition_version(
        definition_version_id=version.id
    )[0]
    assert snapshot[0].variant_id == variant.id


def test_authorization_requires_a_measurement_contract(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)
    _declare_variant(db_session, campaign, experiment, actor, version)
    with pytest.raises(ExecutionAuthorizationNoMeasurementContractError):
        _authorize(db_session, campaign, experiment, actor)


def test_observational_requires_at_least_one_variant_controlled_requires_at_least_two(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)
    _declare_contract(db_session, campaign, experiment, actor, version)
    with pytest.raises(ExecutionAuthorizationInsufficientVariantsError):
        _authorize(db_session, campaign, experiment, actor, key="a-1")
    _declare_variant(db_session, campaign, experiment, actor, version, label="A", key="v-1")
    authorization, _snapshot, created = _authorize(db_session, campaign, experiment, actor, key="a-2")
    assert created is True

    campaign2, _s2, _h2, experiment2, actor2 = build_current_experiment(db_session, campaign_name="Controlled")
    version2 = _define(db_session, campaign2, experiment2, actor2, comparison_type="CONTROLLED", controlled_factors=["Format"])
    _declare_variant(db_session, campaign2, experiment2, actor2, version2, label="Control", key="v-1")
    _declare_contract(db_session, campaign2, experiment2, actor2, version2, success_criterion="CTR improves.")
    with pytest.raises(ExecutionAuthorizationInsufficientVariantsError):
        _authorize(db_session, campaign2, experiment2, actor2, key="a-1")
    _declare_variant(db_session, campaign2, experiment2, actor2, version2, label="Treatment", key="v-2")
    authorization2, snapshot2, created2 = _authorize(db_session, campaign2, experiment2, actor2, key="a-2")
    assert created2 is True and len(snapshot2) == 2


def test_new_variant_after_authorization_is_not_retroactively_included(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _ready(db_session, campaign, experiment, actor)
    _authorization, snapshot, _created = _authorize(db_session, campaign, experiment, actor)
    assert len(snapshot) == 1
    # Declaring a new Variant after Authorization remains legal.
    _declare_variant(db_session, campaign, experiment, actor, version, label="B", key="v-2")
    active = ExecutionAuthorizationRepository(db_session).get_active_for_experiment(
        experiment_id=experiment.id, workspace_id=campaign.workspace_id
    )
    snapshot_after = ExecutionAuthorizationRepository(db_session).list_variants_for_authorization(
        authorization_id=active.id
    )
    assert len(snapshot_after) == 1  # unchanged — the existing Authorization's snapshot is immutable


def test_single_active_authorization_per_experiment_new_request_supersedes_prior(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    _ready(db_session, campaign, experiment, actor)
    first, _snap1, created1 = _authorize(db_session, campaign, experiment, actor, key="a-1")
    assert created1 is True
    second, _snap2, created2 = _authorize(db_session, campaign, experiment, actor, key="a-2", design="Sequential rotation.")
    assert created2 is True
    db_session.refresh(first)
    assert first.revoked_at is not None
    assert first.revoked_reason == "superseded by re-authorization"
    assert first.superseded_by_execution_authorization_id == second.id
    assert second.revoked_at is None
    active = ExecutionAuthorizationRepository(db_session).get_active_for_experiment(
        experiment_id=experiment.id, workspace_id=campaign.workspace_id
    )
    assert active.id == second.id


def test_manual_revocation_and_none_active_error(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    _ready(db_session, campaign, experiment, actor)
    with pytest.raises(ExecutionAuthorizationNoneActiveError):
        ExperimentExecutionAuthorizationService(db_session).revoke(
            campaign=campaign, experiment_public_id=experiment.public_id, reason="none yet", actor_user_id=actor.id,
        )
    authorization, _snapshot, _created = _authorize(db_session, campaign, experiment, actor)
    revoked = ExperimentExecutionAuthorizationService(db_session).revoke(
        campaign=campaign, experiment_public_id=experiment.public_id, reason="mistake", actor_user_id=actor.id,
    )
    assert revoked.id == authorization.id
    assert revoked.revoked_at is not None and revoked.revoked_reason == "mistake"
    assert revoked.superseded_by_execution_authorization_id is None
    with pytest.raises(ExecutionAuthorizationNoneActiveError):
        ExperimentExecutionAuthorizationService(db_session).revoke(
            campaign=campaign, experiment_public_id=experiment.public_id, reason="again", actor_user_id=actor.id,
        )


def test_contract_freeze_while_active_authorization_and_reopens_after_revocation(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _ready(db_session, campaign, experiment, actor)
    _authorize(db_session, campaign, experiment, actor)
    with pytest.raises(MeasurementContractFrozenByAuthorizationError):
        _declare_contract(
            db_session, campaign, experiment, actor, version, key="c-2", base_version=1, success_criterion="x"
        )
    ExperimentExecutionAuthorizationService(db_session).revoke(
        campaign=campaign, experiment_public_id=experiment.public_id, reason="revoked to revise", actor_user_id=actor.id,
    )
    revised = _declare_contract(
        db_session, campaign, experiment, actor, version, key="c-2", base_version=1, success_criterion="x"
    )
    assert revised.version == 2


def test_locks_are_taken_strategy_row_then_experiment_row_and_a_replay_takes_none(db_session, monkeypatch) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    _ready(db_session, campaign, experiment, actor)
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

    _authorize(db_session, campaign, experiment, actor, key="a-1")
    assert order == ["strategy", "experiment"]
    order.clear()
    _row, _snap, created = _authorize(db_session, campaign, experiment, actor, key="a-1")  # matching replay
    assert created is False and order == []


def test_replay_against_since_changed_material_is_a_conflict(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _ready(db_session, campaign, experiment, actor)
    _authorize(db_session, campaign, experiment, actor, key="a-1")
    # Revise the Contract (legal — no active Authorization pins it yet... wait it does).
    # Revoke first so the Contract can be revised, then reuse the SAME key — the server-
    # derived material (contract_version_id) has now changed, so replay must conflict.
    ExperimentExecutionAuthorizationService(db_session).revoke(
        campaign=campaign, experiment_public_id=experiment.public_id, reason="revoked", actor_user_id=actor.id,
    )
    _declare_contract(db_session, campaign, experiment, actor, version, key="c-2", base_version=1, success_criterion="x")
    with pytest.raises(IdempotencyKeyConflictError):
        _authorize(db_session, campaign, experiment, actor, key="a-1")


def test_only_the_one_known_unique_violation_is_translated(db_session, monkeypatch) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    _ready(db_session, campaign, experiment, actor)

    def boom(constraint):
        def create(self, **kwargs):
            raise _integrity_error(constraint)

        return create

    for other in (
        "uq_execution_authorization_variants_authorization_variant", "ck_execution_authorizations_text_fields_nonblank",
        "uq_something_else", None,
    ):
        monkeypatch.setattr(ExecutionAuthorizationRepository, "create", boom(other))
        with pytest.raises(IntegrityError):
            _authorize(db_session, campaign, experiment, actor, key=f"kb-{other}")

    monkeypatch.setattr(ExecutionAuthorizationRepository, "create", boom(_UQ_CLIENT_REQUEST_ID))
    with pytest.raises(IntegrityError):
        _authorize(db_session, campaign, experiment, actor, key="kc")


def test_writer_surface_is_append_only_and_has_no_lifecycle_column() -> None:
    public = {name for name in dir(ExecutionAuthorizationRepository) if not name.startswith("_")}
    assert public == {
        "create", "exists_active_for_contract_version", "get_active_for_experiment",
        "get_by_public_id_for_experiment", "get_by_workspace_and_request_id", "list_for_experiment", "list_variants_for_authorization",
    }
    assert not {
        n for n in dir(ExperimentExecutionAuthorizationService)
        if n.startswith(("update", "delete", "edit", "correct", "retire", "assign", "allocate", "expose"))
    }
    assert hasattr(ExperimentExecutionAuthorizationService, "authorize")
    assert hasattr(ExperimentExecutionAuthorizationService, "revoke")


def test_strategy_stale_rejects_new_writes_but_allows_matching_replay(db_session) -> None:
    from app.strategy.service import StrategyService
    from tests.test_experiment_concurrency import _second_eligible_approval
    from app.orchestration.service import StrategyRevisionService

    campaign, strategy, _h, experiment, actor = build_current_experiment(db_session)
    _ready(db_session, campaign, experiment, actor)
    _authorize(db_session, campaign, experiment, actor, key="a-1")
    approval = _second_eligible_approval(db_session, campaign=campaign, actor_id=actor.id)
    StrategyRevisionService(db_session).revise_strategy(
        campaign=campaign, base_strategy_public_id=strategy.public_id, strategic_approval_public_id=approval.public_id,
        summary="x", positioning_statement="y", actor_user_id=actor.id,
    )
    with pytest.raises(ExecutionAuthorizationStrategyStaleError):
        _authorize(db_session, campaign, experiment, actor, key="a-2")
    # A matching replay of the ALREADY-COMMITTED authorization still succeeds.
    _row, _snap, created = _authorize(db_session, campaign, experiment, actor, key="a-1")
    assert created is False


def test_authorize_and_revoke_write_exactly_one_audit_event_each(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    _ready(db_session, campaign, experiment, actor)
    authorization, _snapshot, _created = _authorize(db_session, campaign, experiment, actor)
    authorized_events = list(
        db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "strategy.execution_authorization.authorized",
                AuditEvent.execution_authorization_id == authorization.id,
            )
        )
    )
    assert len(authorized_events) == 1 and authorized_events[0].previous_state is None

    ExperimentExecutionAuthorizationService(db_session).revoke(
        campaign=campaign, experiment_public_id=experiment.public_id, reason="done", actor_user_id=actor.id,
    )
    revoked_events = list(
        db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "strategy.execution_authorization.revoked",
                AuditEvent.execution_authorization_id == authorization.id,
            )
        )
    )
    assert len(revoked_events) == 1 and revoked_events[0].new_state == "REVOKED"


def test_an_unknown_or_foreign_experiment_public_id_is_non_leaky_forbidden(db_session) -> None:
    """Authorization takes no client-supplied Definition/Contract/Variant
    reference (frozen §B/§C/§12) — unlike Definition/Variant/Contract, there
    is no "foreign resource inside the right Experiment" scenario to test.
    The only resolvable forbidden case is an unknown/cross-tenant Experiment
    public_id itself."""
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    _ready(db_session, campaign, experiment, actor)
    _c2, _s2, _h2, other, _a2 = build_current_experiment(db_session, campaign_name="Tenant Two")
    with pytest.raises(ForbiddenError):
        ExperimentExecutionAuthorizationService(db_session).authorize(
            campaign=campaign, experiment_public_id=other.public_id, client_request_id="a-x",
            unit_of_assignment="visitor", allocation_design="x", actor_user_id=actor.id,
        )


def test_supersession_never_links_across_experiments_or_workspaces(db_session) -> None:
    """§18: the simple self-FK is never permission for arbitrary linkage — the writer only ever
    supersedes the ACTIVE row of the SAME Experiment (and workspace)."""
    campaign1, _s1, _h1, experiment1, actor1 = build_current_experiment(db_session, campaign_name="Tenant One")
    campaign2, _s2, _h2, experiment2, actor2 = build_current_experiment(db_session, campaign_name="Tenant Two")
    _ready(db_session, campaign1, experiment1, actor1)
    _ready(db_session, campaign2, experiment2, actor2)
    first1, _snap, _created = _authorize(db_session, campaign1, experiment1, actor1, key="a-1")
    first2, _snap, _created = _authorize(db_session, campaign2, experiment2, actor2, key="a-1b")
    second2, _snap, _created = _authorize(db_session, campaign2, experiment2, actor2, key="a-2", design="Other.")
    db_session.refresh(first1)
    db_session.refresh(first2)
    assert first1.revoked_at is None and first1.superseded_by_execution_authorization_id is None  # untouched
    assert first2.superseded_by_execution_authorization_id == second2.id
    assert second2.experiment_id == first2.experiment_id and second2.workspace_id == first2.workspace_id
    assert second2.experiment_id != first1.experiment_id and second2.workspace_id != first1.workspace_id
