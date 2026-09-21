"""Service-level tests for Governed Execution Start (frozen Governed Execution
Start Design Freeze): the frozen write order and error precedence, the
temporal boundaries (with a patched server clock — no wall-clock flakiness),
idempotency, the active/configuration gates, Strategy independence (S1), the
permanent Contract (C1) and Variant (V2) freezes, the A2 reauthorization seam,
tenancy concealment, audit semantics, the append-only writer surface and
real-persistence non-effects. All marked `postgres`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import func, inspect, select

from app.audit.models import ActorType, AuditEvent
from app.core.api_errors import (
    ExecutionAuthorizationActiveStartedError,
    ExecutionAuthorizationStrategyStaleError,
    ExecutionStartAlreadyStartedError,
    ExecutionStartAuthorizationNotActiveError,
    ExecutionStartAuthorizationStaleError,
    ExecutionStartTimeInvalidError,
    ExperimentVariantFrozenByExecutionStartError,
    ForbiddenError,
    IdempotencyKeyConflictError,
    MeasurementContractFrozenByAuthorizationError,
    MeasurementContractFrozenByExecutionStartError,
)
from app.persistence.base import Base
from app.strategy import execution_start_service
from app.strategy.execution_authorization_service import ExperimentExecutionAuthorizationService
from app.strategy.execution_start_service import (
    EVENT_EXECUTION_START_ATTESTED,
    EXECUTION_START_FUTURE_TOLERANCE,
    ExperimentExecutionStartService,
)
from app.strategy.models import ExecutionAuthorization, ExecutionStartAttestation, Hypothesis, HypothesisStatus
from app.strategy.repository import ExecutionStartAttestationRepository
from app.strategy.schemas import StartExecutionRequest
from tests.strategytest import build_current_experiment
from tests.test_execution_authorization_domain import (
    _authorize,
    _declare_contract,
    _declare_variant,
    _define,
    _ready,
)

pytestmark = pytest.mark.postgres


def _start(session, campaign, experiment, authorization, actor, *, key="s-1", started_at=None):
    return ExperimentExecutionStartService(session).start(
        campaign=campaign,
        experiment_public_id=experiment.public_id,
        authorization_public_id=authorization.public_id,
        client_request_id=key,
        started_at=started_at if started_at is not None else authorization.created_at + timedelta(seconds=1),
        actor_user_id=actor.id,
    )


def _revoke(session, campaign, experiment, actor, *, reason="Stopping."):
    return ExperimentExecutionAuthorizationService(session).revoke(
        campaign=campaign, experiment_public_id=experiment.public_id, reason=reason, actor_user_id=actor.id
    )


def _authorized(db_session):
    """(campaign, experiment, actor, version, authorization) with one Variant + Contract, authorized."""
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _ready(db_session, campaign, experiment, actor)
    authorization, _snap, _created = _authorize(db_session, campaign, experiment, actor)
    return campaign, experiment, actor, version, authorization


def _start_events(session, experiment) -> list[AuditEvent]:
    """Scoped to THIS test's Experiment: other suites commit their own rows to the shared database."""
    return list(
        session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == EVENT_EXECUTION_START_ATTESTED, AuditEvent.experiment_id == experiment.id
            )
        ).scalars()
    )


def _start_count(session, experiment) -> int:
    return session.scalar(
        select(func.count())
        .select_from(ExecutionStartAttestation)
        .join(ExecutionAuthorization, ExecutionAuthorization.id == ExecutionStartAttestation.authorization_id)
        .where(ExecutionAuthorization.experiment_id == experiment.id)
    )


def _table_counts(session) -> dict[str, int]:
    existing = set(inspect(session.get_bind()).get_table_names())
    return {
        table.name: session.scalar(select(func.count()).select_from(table))
        for table in Base.metadata.sorted_tables
        if table.name in existing
    }


# --- creation / semantics -------------------------------------------------------------------------


def test_start_creates_one_attestation_with_distinct_attested_and_recorded_times(db_session) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    attested = authorization.created_at + timedelta(seconds=30)
    _auth, start, created = _start(db_session, campaign, experiment, authorization, actor, started_at=attested)
    assert created is True
    assert start.public_id.startswith("EXS-") and len(start.public_id) == 16
    assert start.authorization_id == authorization.id and start.workspace_id == campaign.workspace_id
    assert start.started_at == attested  # the attested instant, never clamped
    assert start.created_at is not None  # the server record time, a separate column
    assert _start_count(db_session, experiment) == 1


def test_start_is_identical_for_a_controlled_experiment(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor, comparison_type="CONTROLLED", controlled_factors=["Format"])
    _declare_variant(db_session, campaign, experiment, actor, version, label="A", key="v-1")
    _declare_variant(db_session, campaign, experiment, actor, version, label="B", key="v-2")
    _declare_contract(db_session, campaign, experiment, actor, version, success_criterion="CTR improves.")
    authorization, _snap, _created = _authorize(db_session, campaign, experiment, actor)
    _a, start, created = _start(db_session, campaign, experiment, authorization, actor)
    assert created is True and start.authorization_id == authorization.id
    # A start proves nothing about assignment: no other domain row exists (see the non-effects test).


def test_the_start_leaves_the_authorization_row_unchanged(db_session) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    before = (authorization.revoked_at, authorization.revoked_reason, authorization.superseded_by_execution_authorization_id)
    _start(db_session, campaign, experiment, authorization, actor)
    db_session.refresh(authorization)
    assert (authorization.revoked_at, authorization.revoked_reason, authorization.superseded_by_execution_authorization_id) == before


# --- active-authorization gate ---------------------------------------------------------------------


def test_a_revoked_authorization_cannot_be_started(db_session) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    _revoke(db_session, campaign, experiment, actor)
    with pytest.raises(ExecutionStartAuthorizationNotActiveError):
        _start(db_session, campaign, experiment, authorization, actor)
    assert _start_count(db_session, experiment) == 0 and _start_events(db_session, experiment) == []


def test_a_superseded_authorization_cannot_be_started(db_session) -> None:
    campaign, experiment, actor, _version, first = _authorized(db_session)
    _authorize(db_session, campaign, experiment, actor, key="a-2", design="Different design.")
    db_session.refresh(first)
    assert first.revoked_at is not None and first.superseded_by_execution_authorization_id is not None
    with pytest.raises(ExecutionStartAuthorizationNotActiveError):
        _start(db_session, campaign, experiment, first, actor)
    assert _start_count(db_session, experiment) == 0


# --- idempotency -------------------------------------------------------------------------------------


def test_a_matching_replay_returns_the_original_and_writes_nothing(db_session) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    attested = authorization.created_at + timedelta(seconds=5)
    _a, first, created_first = _start(db_session, campaign, experiment, authorization, actor, started_at=attested)
    _a, second, created_second = _start(db_session, campaign, experiment, authorization, actor, started_at=attested)
    assert (created_first, created_second) == (True, False)
    assert second.id == first.id and _start_count(db_session, experiment) == 1
    assert len(_start_events(db_session, experiment)) == 1  # no duplicate AuditEvent on replay


def test_a_matching_replay_after_revocation_still_returns_the_original(db_session) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    attested = authorization.created_at + timedelta(seconds=5)
    _a, first, _created = _start(db_session, campaign, experiment, authorization, actor, started_at=attested)
    _revoke(db_session, campaign, experiment, actor)
    _a, replay, created = _start(db_session, campaign, experiment, authorization, actor, started_at=attested)
    assert created is False and replay.id == first.id
    assert _start_count(db_session, experiment) == 1 and len(_start_events(db_session, experiment)) == 1


def test_the_same_key_with_a_different_started_at_is_a_conflict(db_session) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    _start(db_session, campaign, experiment, authorization, actor, started_at=authorization.created_at + timedelta(seconds=5))
    with pytest.raises(IdempotencyKeyConflictError):
        _start(db_session, campaign, experiment, authorization, actor, started_at=authorization.created_at + timedelta(seconds=6))
    assert _start_count(db_session, experiment) == 1


def test_the_same_key_for_a_different_authorization_is_a_conflict(db_session) -> None:
    campaign, experiment, actor, _version, first = _authorized(db_session)
    attested = first.created_at + timedelta(seconds=5)
    _start(db_session, campaign, experiment, first, actor, key="shared", started_at=attested)
    _revoke(db_session, campaign, experiment, actor)
    second, _snap, _created = _authorize(db_session, campaign, experiment, actor, key="a-2", design="Second.")
    with pytest.raises(IdempotencyKeyConflictError):
        _start(db_session, campaign, experiment, second, actor, key="shared", started_at=attested)
    assert _start_count(db_session, experiment) == 1


def test_a_different_key_against_an_already_started_authorization_is_already_started(db_session) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    _start(db_session, campaign, experiment, authorization, actor, key="s-1")
    with pytest.raises(ExecutionStartAlreadyStartedError):
        _start(db_session, campaign, experiment, authorization, actor, key="s-2")
    assert _start_count(db_session, experiment) == 1 and len(_start_events(db_session, experiment)) == 1


# --- temporal integrity (patched server clock) --------------------------------------------------------


@pytest.fixture
def frozen_now(monkeypatch):
    """Pins the single server-clock read, relative to the Authorization created in the test."""

    def pin(moment: datetime) -> None:
        monkeypatch.setattr(execution_start_service, "_server_now", lambda: moment)

    return pin


def test_the_frozen_future_tolerance_is_exactly_five_minutes() -> None:
    assert EXECUTION_START_FUTURE_TOLERANCE == timedelta(minutes=5)


def test_started_at_equal_to_the_authorization_creation_is_accepted(db_session, frozen_now) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    frozen_now(authorization.created_at + timedelta(minutes=10))
    _a, start, created = _start(db_session, campaign, experiment, authorization, actor, started_at=authorization.created_at)
    assert created is True and start.started_at == authorization.created_at


def test_started_at_before_the_authorization_creation_is_rejected_with_no_floor_tolerance(db_session, frozen_now) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    frozen_now(authorization.created_at + timedelta(minutes=10))
    with pytest.raises(ExecutionStartTimeInvalidError):
        _start(
            db_session, campaign, experiment, authorization, actor,
            started_at=authorization.created_at - timedelta(microseconds=1),
        )
    assert _start_count(db_session, experiment) == 0 and _start_events(db_session, experiment) == []


def test_started_at_exactly_now_plus_five_minutes_is_accepted(db_session, frozen_now) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    now = authorization.created_at + timedelta(minutes=10)
    frozen_now(now)
    _a, start, created = _start(
        db_session, campaign, experiment, authorization, actor, started_at=now + EXECUTION_START_FUTURE_TOLERANCE
    )
    assert created is True and start.started_at == now + EXECUTION_START_FUTURE_TOLERANCE  # not clamped


def test_started_at_beyond_now_plus_five_minutes_is_rejected(db_session, frozen_now) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    now = authorization.created_at + timedelta(minutes=10)
    frozen_now(now)
    with pytest.raises(ExecutionStartTimeInvalidError):
        _start(
            db_session, campaign, experiment, authorization, actor,
            started_at=now + EXECUTION_START_FUTURE_TOLERANCE + timedelta(microseconds=1),
        )
    assert _start_count(db_session, experiment) == 0


def test_a_naive_datetime_is_rejected_by_the_request_schema() -> None:
    with pytest.raises(ValidationError):
        StartExecutionRequest.model_validate({"client_request_id": "k", "started_at": "2026-01-01T10:00:00"})
    ok = StartExecutionRequest.model_validate({"client_request_id": "k", "started_at": "2026-01-01T10:00:00+02:00"})
    assert ok.started_at.utcoffset() == timedelta(hours=2)


def test_the_request_schema_forbids_extra_fields_and_undecodable_instants() -> None:
    for extra in ("note", "external_reference", "unit_reference", "cohort", "variant_id", "status", "ended_at"):
        with pytest.raises(ValidationError):
            StartExecutionRequest.model_validate(
                {"client_request_id": "k", "started_at": "2026-01-01T10:00:00Z", extra: "x"}
            )
    with pytest.raises(ValidationError):
        StartExecutionRequest.model_validate({"client_request_id": "k", "started_at": "0001-01-01T00:00:00Z"})
    with pytest.raises(ValidationError):
        StartExecutionRequest.model_validate({"client_request_id": "k", "started_at": "9999-12-31T00:00:00Z"})
    with pytest.raises(ValidationError):
        StartExecutionRequest.model_validate({"client_request_id": "  ", "started_at": "2026-01-01T10:00:00Z"})


# --- configuration-current gate (Variant declared between authorization and start) -----------------------


def test_a_variant_declared_after_authorization_makes_the_start_stale_without_touching_the_snapshot(db_session) -> None:
    campaign, experiment, actor, version, authorization = _authorized(db_session)
    _declare_variant(db_session, campaign, experiment, actor, version, label="Late", key="v-late")
    with pytest.raises(ExecutionStartAuthorizationStaleError):
        _start(db_session, campaign, experiment, authorization, actor)
    assert _start_count(db_session, experiment) == 0
    snapshot = ExperimentExecutionAuthorizationService(db_session).authorizations.list_variants_for_authorization(
        authorization_id=authorization.id
    )
    assert len(snapshot) == 1  # the historical snapshot is never refreshed or mutated


def test_reauthorizing_after_the_late_variant_then_allows_the_start(db_session) -> None:
    campaign, experiment, actor, version, first = _authorized(db_session)
    _declare_variant(db_session, campaign, experiment, actor, version, label="Late", key="v-late")
    second, snapshot, _created = _authorize(db_session, campaign, experiment, actor, key="a-2", design="Second.")
    assert len(snapshot) == 2
    _a, start, created = _start(db_session, campaign, experiment, second, actor)
    assert created is True and start.authorization_id == second.id


# --- error precedence (frozen write order) -------------------------------------------------------------------


def test_already_started_takes_precedence_over_not_active(db_session) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    _start(db_session, campaign, experiment, authorization, actor, key="s-1")
    _revoke(db_session, campaign, experiment, actor)
    with pytest.raises(ExecutionStartAlreadyStartedError):
        _start(db_session, campaign, experiment, authorization, actor, key="s-2")


def test_not_active_takes_precedence_over_stale(db_session) -> None:
    campaign, experiment, actor, version, authorization = _authorized(db_session)
    _declare_variant(db_session, campaign, experiment, actor, version, label="Late", key="v-late")
    _revoke(db_session, campaign, experiment, actor)
    with pytest.raises(ExecutionStartAuthorizationNotActiveError):
        _start(db_session, campaign, experiment, authorization, actor)


def test_stale_takes_precedence_over_an_invalid_time(db_session) -> None:
    campaign, experiment, actor, version, authorization = _authorized(db_session)
    _declare_variant(db_session, campaign, experiment, actor, version, label="Late", key="v-late")
    with pytest.raises(ExecutionStartAuthorizationStaleError):
        _start(
            db_session, campaign, experiment, authorization, actor,
            started_at=authorization.created_at - timedelta(days=1),
        )


# --- Strategy independence (S1) -----------------------------------------------------------------------------


def test_a_start_may_still_be_attested_after_a_later_strategy_revision(db_session) -> None:
    from app.orchestration.service import StrategyRevisionService
    from tests.test_experiment_concurrency import _second_eligible_approval

    campaign, strategy, _h, experiment, actor = build_current_experiment(db_session)
    _ready(db_session, campaign, experiment, actor)
    authorization, _snap, _created = _authorize(db_session, campaign, experiment, actor)
    approval = _second_eligible_approval(db_session, campaign=campaign, actor_id=actor.id)
    StrategyRevisionService(db_session).revise_strategy(
        campaign=campaign, base_strategy_public_id=strategy.public_id, strategic_approval_public_id=approval.public_id,
        summary="x", positioning_statement="y", actor_user_id=actor.id,
    )
    # Authorization's own create-time Strategy currency is NOT weakened ...
    with pytest.raises(ExecutionAuthorizationStrategyStaleError):
        _authorize(db_session, campaign, experiment, actor, key="a-2", design="New.")
    # ... but the already-granted, still-active Authorization can be started.
    _a, start, created = _start(db_session, campaign, experiment, authorization, actor)
    assert created is True and start.authorization_id == authorization.id
    db_session.refresh(authorization)
    assert authorization.revoked_at is None  # never invalidated by the later Strategy revision


# --- Contract post-start freeze (C1) ---------------------------------------------------------------------------


def test_contract_freeze_matrix_unstarted_and_started_authorizations(db_session) -> None:
    campaign, experiment, actor, version, authorization = _authorized(db_session)
    # unstarted + active -> frozen by the Authorization (existing behavior)
    with pytest.raises(MeasurementContractFrozenByAuthorizationError):
        _declare_contract(db_session, campaign, experiment, actor, version, key="c-2", base_version=1, measurement_window_days=7)
    # unstarted + revoked -> the revision reopens (existing recovery is preserved)
    _revoke(db_session, campaign, experiment, actor)
    revised = _declare_contract(db_session, campaign, experiment, actor, version, key="c-2", base_version=1, measurement_window_days=7)
    assert revised.version == 2
    # A new Authorization pins the new tip; start it; the lineage is now permanently frozen.
    second, _snap, _created = _authorize(db_session, campaign, experiment, actor, key="a-2", design="Second.")
    _start(db_session, campaign, experiment, second, actor)
    with pytest.raises(MeasurementContractFrozenByExecutionStartError):  # started + active
        _declare_contract(db_session, campaign, experiment, actor, version, key="c-3", base_version=2, measurement_window_days=9)
    _revoke(db_session, campaign, experiment, actor)
    with pytest.raises(MeasurementContractFrozenByExecutionStartError):  # started + REVOKED: still frozen (mandatory)
        _declare_contract(db_session, campaign, experiment, actor, version, key="c-3", base_version=2, measurement_window_days=9)


def test_the_start_freeze_takes_precedence_over_the_authorization_freeze_deterministically(db_session) -> None:
    campaign, experiment, actor, version, authorization = _authorized(db_session)
    _start(db_session, campaign, experiment, authorization, actor)
    with pytest.raises(MeasurementContractFrozenByExecutionStartError):  # both freezes apply; the start code wins
        _declare_contract(db_session, campaign, experiment, actor, version, key="c-2", base_version=1, measurement_window_days=7)


def test_a_stale_base_version_is_reported_before_the_start_freeze(db_session) -> None:
    from app.core.api_errors import MeasurementContractBaseStaleError

    campaign, experiment, actor, version, authorization = _authorized(db_session)
    _start(db_session, campaign, experiment, authorization, actor)
    with pytest.raises(MeasurementContractBaseStaleError):
        _declare_contract(db_session, campaign, experiment, actor, version, key="c-2", base_version=0, measurement_window_days=7)


def test_a_replay_of_an_earlier_contract_write_still_replays_after_the_start(db_session) -> None:
    from app.strategy.measurement_contract_service import ExperimentMeasurementContractService

    campaign, experiment, actor, version, authorization = _authorized(db_session)
    _start(db_session, campaign, experiment, authorization, actor)
    signal = {
        "name": "Click-through rate", "description": "CTR.", "expected_direction": None,
        "evidence_requirement": None, "tracking_required": False,
    }
    # The seed Contract used key "c-1" at base_version 0; an identical retry is a replay, not a frozen error.
    result = ExperimentMeasurementContractService(db_session).declare_or_revise(
        campaign=campaign, experiment_public_id=experiment.public_id, base_version=0, client_request_id="c-1",
        definition_version_public_id=version.public_id, signals=[signal], actor_user_id=actor.id,
        measurement_window_days=None, minimum_evidence=None, success_criterion=None,
        analysis_method_intent=None, stopping_rule=None, decision_rule_intent=None,
    )
    assert result[3] is False and result[0].version == 1


# --- Variant post-start freeze (V2) ----------------------------------------------------------------------------


def test_variant_declaration_is_legal_before_the_start_and_refused_permanently_after(db_session) -> None:
    campaign, experiment, actor, version, authorization = _authorized(db_session)
    _revoke(db_session, campaign, experiment, actor)  # no Start yet: legal (existing rules)
    _declare_variant(db_session, campaign, experiment, actor, version, label="Pre", key="v-pre")
    second, snapshot, _created = _authorize(db_session, campaign, experiment, actor, key="a-2", design="Second.")
    assert len(snapshot) == 2
    _start(db_session, campaign, experiment, second, actor)
    with pytest.raises(ExperimentVariantFrozenByExecutionStartError):  # after Start (active)
        _declare_variant(db_session, campaign, experiment, actor, version, label="Post", key="v-post")
    _revoke(db_session, campaign, experiment, actor)
    with pytest.raises(ExperimentVariantFrozenByExecutionStartError):  # after Start + revoke: STILL refused
        _declare_variant(db_session, campaign, experiment, actor, version, label="Post", key="v-post")


def test_a_replay_of_an_earlier_variant_declaration_still_replays_after_the_start(db_session) -> None:
    campaign, experiment, actor, version, authorization = _authorized(db_session)
    _start(db_session, campaign, experiment, authorization, actor)
    from app.strategy.variant_service import ExperimentVariantService

    _variant, _pinned, created = ExperimentVariantService(db_session).declare_variant(
        campaign=campaign, experiment_public_id=experiment.public_id, definition_version_public_id=version.public_id,
        label="A", condition_description="d", client_request_id="v-1", actor_user_id=actor.id,
    )
    assert created is False  # the retry of the seed declaration replays; it is not a "frozen" error


# --- reauthorization (A2) -------------------------------------------------------------------------------------------


def test_an_unstarted_active_authorization_is_still_auto_superseded(db_session) -> None:
    campaign, experiment, actor, _version, first = _authorized(db_session)
    second, _snap, created = _authorize(db_session, campaign, experiment, actor, key="a-2", design="Second.")
    assert created is True
    db_session.refresh(first)
    assert first.revoked_at is not None and first.superseded_by_execution_authorization_id == second.id


def test_a_started_active_authorization_is_never_silently_superseded(db_session) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    _start(db_session, campaign, experiment, authorization, actor)
    with pytest.raises(ExecutionAuthorizationActiveStartedError):
        _authorize(db_session, campaign, experiment, actor, key="a-2", design="Second.")
    db_session.refresh(authorization)
    assert authorization.revoked_at is None  # no implicit revocation
    assert db_session.scalar(
        select(func.count()).select_from(ExecutionAuthorization).where(ExecutionAuthorization.experiment_id == experiment.id)
    ) == 1


def test_started_then_explicitly_revoked_then_a_new_authorization_has_independent_provenance(db_session) -> None:
    campaign, experiment, actor, _version, first = _authorized(db_session)
    _a, start_a, _created = _start(db_session, campaign, experiment, first, actor, key="s-a")
    _revoke(db_session, campaign, experiment, actor)
    second, snapshot, created = _authorize(db_session, campaign, experiment, actor, key="a-2", design="Second.")
    assert created is True and len(snapshot) == 1
    assert second.definition_version_id == first.definition_version_id
    assert second.contract_version_id == first.contract_version_id
    _a, start_b, created_b = _start(db_session, campaign, experiment, second, actor, key="s-b")
    assert created_b is True and start_b.id != start_a.id
    repo = ExecutionStartAttestationRepository(db_session)
    assert repo.get_for_authorization(authorization_id=first.id).id == start_a.id  # Start A stays on A
    assert repo.get_for_authorization(authorization_id=second.id).id == start_b.id  # Start B on B


# --- tenancy concealment -------------------------------------------------------------------------------------------------


def test_unknown_foreign_or_cross_experiment_targets_are_non_leaky_forbidden(db_session) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    other_campaign, _s2, _h2, other_experiment, other_actor = build_current_experiment(db_session, campaign_name="Tenant Two")
    other_version = _ready(db_session, other_campaign, other_experiment, other_actor)
    other_auth, _snap, _created = _authorize(db_session, other_campaign, other_experiment, other_actor)

    service = ExperimentExecutionStartService(db_session)
    attested = authorization.created_at + timedelta(seconds=1)

    def call(*, camp, exp_public, auth_public):
        return service.start(
            campaign=camp, experiment_public_id=exp_public, authorization_public_id=auth_public,
            client_request_id="k", started_at=attested, actor_user_id=actor.id,
        )

    with pytest.raises(ForbiddenError):
        call(camp=campaign, exp_public=experiment.public_id, auth_public="EXA-DOESNOTEXIST")
    with pytest.raises(ForbiddenError):  # a real Authorization of ANOTHER tenant's Experiment
        call(camp=campaign, exp_public=experiment.public_id, auth_public=other_auth.public_id)
    with pytest.raises(ForbiddenError):  # a foreign Experiment id under this campaign
        call(camp=campaign, exp_public=other_experiment.public_id, auth_public=authorization.public_id)
    assert other_version is not None
    assert _start_count(db_session, experiment) == 0


# --- audit -------------------------------------------------------------------------------------------------------------


def test_one_user_audit_event_is_written_on_creation_with_the_frozen_state_semantics(db_session) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    _a, start, _created = _start(db_session, campaign, experiment, authorization, actor)
    events = _start_events(db_session, experiment)
    assert len(events) == 1
    event = events[0]
    assert event.actor_type == ActorType.USER and event.actor_user_id == actor.id
    assert event.previous_state is None
    assert event.new_state == f"attested:{authorization.public_id}"
    assert event.execution_start_attestation_id == start.id
    assert event.execution_authorization_id == authorization.id
    assert event.experiment_id == experiment.id and event.campaign_id == campaign.id
    assert event.workspace_id == campaign.workspace_id
    # EXSTART-IMPL-OBS-1: no strategy_id/hypothesis_id — an audit FK to the Strategy row would implicitly
    # KEY SHARE-lock it under the Experiment lock and invert the canonical order (deadlock; see concurrency tests).
    assert event.strategy_id is None and event.hypothesis_id is None


def test_no_audit_event_is_written_on_conflict_or_validation_failure(db_session) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    with pytest.raises(ExecutionStartTimeInvalidError):
        _start(db_session, campaign, experiment, authorization, actor, started_at=authorization.created_at - timedelta(days=1))
    assert _start_events(db_session, experiment) == []


# --- authority / immutability / writer surface ----------------------------------------------------------------------------


def test_the_writer_surface_is_append_only_and_has_no_system_or_agent_path(db_session) -> None:
    import inspect as pyinspect

    repo_public = {n for n in dir(ExecutionStartAttestationRepository) if not n.startswith("_")}
    assert repo_public == {
        "create", "exists_for_authorization", "exists_for_experiment", "get_by_workspace_and_request_id",
        "get_for_authorization",
    }
    service_public = {n for n in dir(ExperimentExecutionStartService) if not n.startswith("_")}
    assert service_public == {"start"}
    parameters = set(pyinspect.signature(ExperimentExecutionStartService.start).parameters)
    assert "actor_user_id" in parameters and not {"actor_type", "actor", "system", "agent"} & parameters
    assert {c.name for c in ExecutionStartAttestation.__table__.columns} == {
        "id", "public_id", "workspace_id", "authorization_id", "started_at", "created_at", "client_request_id",
    }
    assert ExecutionStartAttestation.__table__.c.started_at.server_default is None  # attested, never server-defaulted
    assert ExecutionStartAttestation.__table__.c.created_at.server_default is not None


# --- non-effects (real persistence evidence) -----------------------------------------------------------------------------------


def test_a_start_writes_only_its_own_row_and_one_audit_event(db_session) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    hypothesis_status = db_session.scalar(select(Hypothesis.status).where(Hypothesis.id == experiment.hypothesis_id))
    before = _table_counts(db_session)
    _start(db_session, campaign, experiment, authorization, actor)
    after = _table_counts(db_session)
    changed = {name: after[name] - before[name] for name in after if after[name] != before[name]}
    # Every other table — Content, Distribution, TrackingRequirement, CommercialOutcome, Metric/Measurement,
    # Learning, StrategicDecision, StrategyRevision, Variant, Contract, Authorization — is untouched, and no
    # Assignment/Exposure/Result/Winner/Verdict table exists at all.
    assert changed == {"execution_start_attestations": 1, "audit_events": 1}
    assert db_session.scalar(select(Hypothesis.status).where(Hypothesis.id == experiment.hypothesis_id)) == hypothesis_status
    assert hypothesis_status == HypothesisStatus.OPEN  # the ungoverned verdict transition is never invoked
    assert not [t for t in Base.metadata.tables if any(w in t for w in ("assignment", "exposure", "winner", "verdict"))]


# --- defensive translation of the DB unique backstops (lock protocol bypassed on purpose) --------------------------------------


def test_a_real_authorization_unique_violation_is_translated_to_already_started(db_session, monkeypatch) -> None:
    """The lock protocol makes this path unreachable in normal operation; here the pre-checks are blinded so the
    INSERT itself hits the real ``UNIQUE(authorization_id)`` and only the known-constraint translation saves it."""
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    _start(db_session, campaign, experiment, authorization, actor, key="s-1")
    monkeypatch.setattr(ExecutionStartAttestationRepository, "exists_for_authorization", lambda self, **_kw: False)
    with pytest.raises(ExecutionStartAlreadyStartedError):
        _start(db_session, campaign, experiment, authorization, actor, key="s-2")
    monkeypatch.undo()
    assert _start_count(db_session, experiment) == 1 and len(_start_events(db_session, experiment)) == 1


def test_a_real_client_request_id_unique_violation_is_translated_to_a_replay(db_session, monkeypatch) -> None:
    campaign, experiment, actor, _version, authorization = _authorized(db_session)
    attested = authorization.created_at + timedelta(seconds=5)
    _a, first, _created = _start(db_session, campaign, experiment, authorization, actor, key="dup", started_at=attested)
    real = ExecutionStartAttestationRepository.get_by_workspace_and_request_id
    calls = {"n": 0}

    def blinded(self, **kwargs):  # the two pre-insert replay lookups miss; the post-violation re-read sees the truth
        calls["n"] += 1
        return None if calls["n"] <= 2 else real(self, **kwargs)

    monkeypatch.setattr(ExecutionStartAttestationRepository, "get_by_workspace_and_request_id", blinded)
    monkeypatch.setattr(ExecutionStartAttestationRepository, "exists_for_authorization", lambda self, **_kw: False)
    _auth, replay, created = _start(db_session, campaign, experiment, authorization, actor, key="dup", started_at=attested)
    monkeypatch.undo()
    assert created is False and replay.id == first.id
    assert _start_count(db_session, experiment) == 1 and len(_start_events(db_session, experiment)) == 1
