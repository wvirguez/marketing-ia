"""Audit attribution and atomicity for Content persistence (BACKEND-10
§32/§33). All marked `postgres`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.audit.models import AuditEvent
from app.audit.repository import AuditEventRepository
from app.content.models import ContentApprovalStatus, ContentBrief, ContentPiece, ContentVersion
from app.content.repository import ContentVersionRepository
from app.content.service import ContentService
from tests.contenttest import build_plan_with_item, default_piece_fields, default_version_payload, make_user

pytestmark = pytest.mark.postgres


def _total_count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def _record_brief(session, content_campaign):
    _campaign, _run, _stages, plan, item = content_campaign
    return ContentService(session).record_brief(plan_item=item, content_plan=plan, brief="Produce a beginner reel.")


def _record_piece(session, brief):
    return ContentService(session).record_piece(
        content_brief=brief, initial_payload=default_version_payload(), **default_piece_fields()
    )


# --- exact attribution ------------------------------------------------


def test_brief_recorded_event_identifies_the_exact_brief(content_campaign, db_session) -> None:
    brief = _record_brief(db_session, content_campaign)
    events = db_session.execute(select(AuditEvent).where(AuditEvent.event_type == "content.brief.recorded")).scalars().all()
    matching = [e for e in events if e.content_brief_id == brief.id]
    assert len(matching) == 1
    assert matching[0].plan_item_id == brief.plan_item_id
    assert matching[0].content_plan_id == brief.content_plan_id


def test_piece_and_version_recorded_events_identify_exact_rows(content_campaign, db_session) -> None:
    brief = _record_brief(db_session, content_campaign)
    piece, version = _record_piece(db_session, brief)

    piece_events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "content.piece.recorded", AuditEvent.content_piece_id == piece.id)
    ).scalars().all()
    assert len(piece_events) == 1

    version_events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "content.version.recorded", AuditEvent.content_version_id == version.id
        )
    ).scalars().all()
    assert len(version_events) == 1
    assert version_events[0].content_piece_id == piece.id


def test_two_pieces_created_close_together_are_never_confused(content_campaign, db_session) -> None:
    """Two Briefs/Pieces created back-to-back must still be identified
    exactly — never by inference from "the latest row" or positional
    ordering."""
    plan_item_a = content_campaign[4]
    campaign, run, stages, plan, _item = content_campaign
    service = ContentService(db_session)
    brief_a = service.record_brief(plan_item=plan_item_a, content_plan=plan, brief="Brief A.")
    piece_a, _va = service.record_piece(content_brief=brief_a, initial_payload=default_version_payload(), **default_piece_fields())

    _c2, _r2, stages2, plan2, item_b = build_plan_with_item(db_session, campaign_name="Second Campaign")
    brief_b = service.record_brief(plan_item=item_b, content_plan=plan2, brief="Brief B.")
    piece_b, _vb = service.record_piece(content_brief=brief_b, initial_payload=default_version_payload(), **default_piece_fields())

    events = db_session.execute(select(AuditEvent).where(AuditEvent.event_type == "content.piece.recorded")).scalars().all()
    matching_a = [e for e in events if e.content_piece_id == piece_a.id]
    matching_b = [e for e in events if e.content_piece_id == piece_b.id]
    assert len(matching_a) == 1
    assert len(matching_b) == 1
    assert matching_a[0].id != matching_b[0].id


def test_approved_transaction_records_both_decision_and_status_change_attribution(content_campaign, db_session) -> None:
    campaign, *_ = content_campaign
    brief = _record_brief(db_session, content_campaign)
    piece, version = _record_piece(db_session, brief)
    service = ContentService(db_session)
    service.mark_in_production(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
    service.mark_produced(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
    service.mark_ready_for_review(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
    approval = service.request_approval(content_version=version)
    service.mark_under_review(workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id)
    reviewer = make_user(db_session)

    service.record_authorized_approval_decision(
        workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id,
        decision=ContentApprovalStatus.APPROVED, actor_user_id=reviewer.id,
    )

    decision_events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "content.approval.decision_recorded", AuditEvent.content_approval_id == approval.id
        )
    ).scalars().all()
    assert len(decision_events) == 1
    assert decision_events[0].actor_user_id == reviewer.id
    assert decision_events[0].new_state == "APPROVED"

    status_events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "content.piece.status_changed", AuditEvent.content_piece_id == piece.id,
            AuditEvent.content_approval_id == approval.id,
        )
    ).scalars().all()
    assert len(status_events) == 1
    assert status_events[0].previous_state == "READY_FOR_REVIEW"
    assert status_events[0].new_state == "APPROVED"
    assert status_events[0].actor_user_id == reviewer.id


# --- atomicity: rollback on failure --------------------------------------


def test_audit_failure_rolls_back_the_content_brief(content_campaign, db_session) -> None:
    _campaign, _run, _stages, plan, item = content_campaign
    briefs_before = _total_count(db_session, ContentBrief)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            ContentService(db_session).record_brief(plan_item=item, content_plan=plan, brief="should not persist")

    db_session.rollback()
    assert _total_count(db_session, ContentBrief) == briefs_before


def test_audit_failure_rolls_back_piece_and_initial_version_together(content_campaign, db_session) -> None:
    brief = _record_brief(db_session, content_campaign)
    pieces_before = _total_count(db_session, ContentPiece)
    versions_before = _total_count(db_session, ContentVersion)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            ContentService(db_session).record_piece(
                content_brief=brief, initial_payload=default_version_payload(), **default_piece_fields()
            )

    db_session.rollback()
    assert _total_count(db_session, ContentPiece) == pieces_before
    assert _total_count(db_session, ContentVersion) == versions_before


def test_child_version_failure_rolls_back_the_parent_piece_too(content_campaign, db_session) -> None:
    brief = _record_brief(db_session, content_campaign)
    pieces_before = _total_count(db_session, ContentPiece)

    with patch.object(ContentVersionRepository, "create", side_effect=RuntimeError("simulated version failure")):
        with pytest.raises(RuntimeError, match="simulated version failure"):
            ContentService(db_session).record_piece(
                content_brief=brief, initial_payload=default_version_payload(), **default_piece_fields()
            )

    db_session.rollback()
    assert _total_count(db_session, ContentPiece) == pieces_before


def test_audit_failure_rolls_back_a_new_version(content_campaign, db_session) -> None:
    brief = _record_brief(db_session, content_campaign)
    piece, _v1 = _record_piece(db_session, brief)
    versions_before = _total_count(db_session, ContentVersion)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            ContentService(db_session).record_version(content_piece=piece, payload=default_version_payload())

    db_session.rollback()
    assert _total_count(db_session, ContentVersion) == versions_before


def test_piece_transition_failure_rolls_back_the_approval_decision(content_campaign, db_session) -> None:
    """If the Piece-side transition were to fail, the Approval decision
    must not survive either — one transaction, both writes or neither."""
    campaign, *_ = content_campaign
    brief = _record_brief(db_session, content_campaign)
    piece, version = _record_piece(db_session, brief)
    service = ContentService(db_session)
    # Deliberately leave the Piece at DRAFT (never brought to
    # READY_FOR_REVIEW) so the coupled Piece transition is illegal.
    approval = service.request_approval(content_version=version)
    service.mark_under_review(workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id)
    reviewer = make_user(db_session)

    from app.core.api_errors import InvalidLifecycleTransitionError

    with pytest.raises(InvalidLifecycleTransitionError):
        service.record_authorized_approval_decision(
            workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id,
            decision=ContentApprovalStatus.APPROVED, actor_user_id=reviewer.id,
        )
    db_session.rollback()

    from app.content.models import ContentApproval

    reloaded = db_session.execute(
        select(ContentApproval).where(ContentApproval.id == approval.id)
    ).scalar_one()
    assert reloaded.status is ContentApprovalStatus.UNDER_REVIEW, "the decision must not survive if the coupled transition failed"


def test_audit_failure_rolls_back_the_approved_coupling(content_campaign, db_session) -> None:
    campaign, *_ = content_campaign
    brief = _record_brief(db_session, content_campaign)
    piece, version = _record_piece(db_session, brief)
    service = ContentService(db_session)
    service.mark_in_production(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
    service.mark_produced(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
    service.mark_ready_for_review(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
    approval = service.request_approval(content_version=version)
    service.mark_under_review(workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id)
    reviewer = make_user(db_session)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            service.record_authorized_approval_decision(
                workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id,
                decision=ContentApprovalStatus.APPROVED, actor_user_id=reviewer.id,
            )
    db_session.rollback()

    from app.content.models import ContentApproval

    reloaded_approval = db_session.execute(select(ContentApproval).where(ContentApproval.id == approval.id)).scalar_one()
    reloaded_piece = db_session.execute(select(ContentPiece).where(ContentPiece.id == piece.id)).scalar_one()
    assert reloaded_approval.status is ContentApprovalStatus.UNDER_REVIEW
    assert reloaded_piece.status.value == "READY_FOR_REVIEW"
