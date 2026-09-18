"""Audit attribution and atomicity for StrategicApproval (MVP-29B, frozen
MVP-29A contract §R). All marked `postgres`."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.audit.models import ActorType, AuditEvent
from app.audit.repository import AuditEventRepository
from app.orchestration.models import StrategicApproval, StrategicApprovalOutcome
from app.orchestration.service import EVENT_STRATEGIC_APPROVAL_RECORDED, StrategicApprovalService
from tests.orchestrationtest import build_strategic_approval, build_strategic_decision

pytestmark = pytest.mark.postgres


def _total_count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


# --- exact attribution -------------------------------------------------------


def test_recorded_event_identifies_the_exact_approval_and_actor(db_session) -> None:
    campaign, _recommendation, _decision, approval, actor = build_strategic_approval(
        db_session, outcome=StrategicApprovalOutcome.APPROVED
    )
    event = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == EVENT_STRATEGIC_APPROVAL_RECORDED, AuditEvent.strategic_approval_id == approval.id
        )
    )
    assert event is not None
    assert event.workspace_id == approval.workspace_id
    assert event.campaign_id == campaign.id
    assert event.actor_type == ActorType.USER
    assert event.actor_user_id == actor.id
    assert event.new_state == StrategicApprovalOutcome.APPROVED.value


def test_rejected_outcome_is_reflected_in_the_audit_event(db_session) -> None:
    _campaign, _recommendation, _decision, approval, _actor = build_strategic_approval(
        db_session, outcome=StrategicApprovalOutcome.REJECTED
    )
    event = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == EVENT_STRATEGIC_APPROVAL_RECORDED, AuditEvent.strategic_approval_id == approval.id
        )
    )
    assert event is not None
    assert event.new_state == StrategicApprovalOutcome.REJECTED.value


def test_actor_user_id_is_mandatory_never_system(db_session) -> None:
    """Unlike Commercial's own lower-weight record-keeping actions,
    recording a StrategicApproval is an OWNER/ADMIN-gated governance
    action — ``actor_user_id`` has no default and is always a real human,
    never ``ActorType.SYSTEM`` (MVP-29A §H)."""
    import inspect

    params = inspect.signature(StrategicApprovalService.record_approval).parameters
    assert params["actor_user_id"].default is inspect.Parameter.empty


# --- atomicity: no partially-audited successful approval ---------------------


def test_no_partial_approval_survives_a_mid_record_failure(db_session) -> None:
    campaign, _recommendation, decision, actor = build_strategic_decision(db_session)
    service = StrategicApprovalService(db_session)
    events_before = _total_count(db_session, AuditEvent)
    approvals_before = _total_count(db_session, StrategicApproval)

    def _fail(self, *args, **kwargs):
        raise RuntimeError("simulated mid-record audit failure")

    with patch.object(AuditEventRepository, "record", _fail):
        with pytest.raises(RuntimeError, match="simulated mid-record audit failure"):
            service.record_approval(
                campaign=campaign, decision_public_id=decision.public_id,
                outcome=StrategicApprovalOutcome.APPROVED, actor_user_id=actor.id,
            )

    db_session.rollback()

    assert _total_count(db_session, AuditEvent) == events_before
    assert _total_count(db_session, StrategicApproval) == approvals_before
