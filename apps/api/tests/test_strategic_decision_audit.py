"""Audit attribution and atomicity for StrategicDecision (MVP-28B, frozen
MVP-28A/-R1/-R2 contract §N). All marked `postgres`."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.audit.models import ActorType, AuditEvent
from app.audit.repository import AuditEventRepository
from app.orchestration.models import StrategicDecision, StrategicDecisionType
from app.orchestration.service import (
    EVENT_STRATEGIC_DECISION_RECORDED,
    EVENT_STRATEGIC_DECISION_SUPERSEDED,
    StrategicDecisionService,
)
from tests.orchestrationtest import build_accepted_recommendation, build_strategic_decision

pytestmark = pytest.mark.postgres


def _total_count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


# --- exact attribution -------------------------------------------------------


def test_recorded_event_identifies_the_exact_decision_and_actor(db_session) -> None:
    campaign, recommendation, decision, actor = build_strategic_decision(db_session)
    event = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == EVENT_STRATEGIC_DECISION_RECORDED, AuditEvent.strategic_decision_id == decision.id
        )
    )
    assert event is not None
    assert event.workspace_id == decision.workspace_id
    assert event.campaign_id == campaign.id
    assert event.actor_type == ActorType.USER
    assert event.actor_user_id == actor.id
    assert event.new_state == StrategicDecisionType.ADOPT.value


def test_supersession_emits_both_events_in_the_same_request_correlation(db_session) -> None:
    campaign, _recommendation, original, actor = build_strategic_decision(db_session)
    replacement = StrategicDecisionService(db_session).supersede_decision(
        campaign=campaign, decision_public_id=original.public_id, decision_type=StrategicDecisionType.DEFER,
        statement="New version.", actor_user_id=actor.id, request_id="req-strategic-decision-1",
    )
    recorded = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == EVENT_STRATEGIC_DECISION_RECORDED, AuditEvent.strategic_decision_id == replacement.id
        )
    )
    superseded = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == EVENT_STRATEGIC_DECISION_SUPERSEDED, AuditEvent.strategic_decision_id == original.id
        )
    )
    assert recorded is not None and superseded is not None
    assert recorded.request_id == superseded.request_id == "req-strategic-decision-1"
    # Authoritative link lives on the domain row itself, never only in new_state.
    assert original.superseded_by_strategic_decision_id == replacement.id
    # new_state is human-legibility only, mirroring the existing
    # f"supersedes:{...}"/f"superseded_by:{...}" convention from Commercial.
    assert recorded.new_state == f"supersedes:{original.public_id}"
    assert superseded.new_state == f"superseded_by:{replacement.public_id}"


def test_actor_user_id_is_mandatory_never_system(db_session) -> None:
    """Unlike Commercial's own lower-weight record-keeping actions,
    recording/superseding a StrategicDecision is an OWNER/ADMIN-gated
    governance action — ``actor_user_id`` has no default and is always a
    real human, never ``ActorType.SYSTEM`` (MVP-28A-R2 §K/§N)."""
    import inspect

    record_params = inspect.signature(StrategicDecisionService.record_decision).parameters
    supersede_params = inspect.signature(StrategicDecisionService.supersede_decision).parameters
    assert record_params["actor_user_id"].default is inspect.Parameter.empty
    assert supersede_params["actor_user_id"].default is inspect.Parameter.empty


# --- atomicity: no partially-audited successful supersession -----------------


def test_no_partial_audit_or_replacement_survives_a_mid_supersession_failure(db_session) -> None:
    """A failure recording the *second* (superseded) audit event must roll
    back the already-flushed replacement row, the original's already-set
    disposition columns, and the first (recorded) audit event too —
    mirrors ``tests/test_commercial_audit.py``'s own equivalent test
    exactly."""
    campaign, _recommendation, original, actor = build_strategic_decision(db_session)
    service = StrategicDecisionService(db_session)
    events_before = _total_count(db_session, AuditEvent)
    decisions_before = _total_count(db_session, StrategicDecision)

    real_record = AuditEventRepository.record
    call_count = {"n": 0}

    def _fail_on_second_call(self, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:  # replacement "recorded" event (1) succeeds, original "superseded" event (2) fails
            raise RuntimeError("simulated mid-supersession audit failure")
        return real_record(self, *args, **kwargs)

    with patch.object(AuditEventRepository, "record", _fail_on_second_call):
        with pytest.raises(RuntimeError, match="simulated mid-supersession audit failure"):
            service.supersede_decision(
                campaign=campaign, decision_public_id=original.public_id, decision_type=StrategicDecisionType.DECLINE,
                statement="should not persist", actor_user_id=actor.id,
            )

    db_session.rollback()

    assert _total_count(db_session, AuditEvent) == events_before
    assert _total_count(db_session, StrategicDecision) == decisions_before
    db_session.refresh(original)
    assert original.superseded_at is None
    assert original.superseded_by_strategic_decision_id is None


def test_no_partial_decision_survives_a_mid_record_failure(db_session) -> None:
    campaign, recommendation, actor = build_accepted_recommendation(db_session)
    service = StrategicDecisionService(db_session)
    events_before = _total_count(db_session, AuditEvent)
    decisions_before = _total_count(db_session, StrategicDecision)

    real_record = AuditEventRepository.record

    def _fail(self, *args, **kwargs):
        raise RuntimeError("simulated mid-record audit failure")

    with patch.object(AuditEventRepository, "record", _fail):
        with pytest.raises(RuntimeError, match="simulated mid-record audit failure"):
            service.record_decision(
                campaign=campaign, recommendation_public_id=recommendation.public_id,
                decision_type=StrategicDecisionType.ADOPT, statement="should not persist", actor_user_id=actor.id,
            )

    db_session.rollback()

    assert _total_count(db_session, AuditEvent) == events_before
    assert _total_count(db_session, StrategicDecision) == decisions_before
