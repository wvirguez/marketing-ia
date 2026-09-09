"""Content domain persistence, provenance, tenancy, cardinality, state
machine, and governance-boundary tests (BACKEND-10). All marked `postgres`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.content.models import ContentApproval, ContentBrief, ContentPiece, ContentPieceStatus, ContentVersion, ContentApprovalStatus
from app.content.service import ContentService
from app.content.transitions import (
    CONTENT_APPROVAL_TRANSITIONS,
    CONTENT_PIECE_TRANSITIONS,
    is_legal_content_approval_transition,
    is_legal_content_piece_transition,
)
from app.core.api_errors import ForbiddenError, InvalidLifecycleTransitionError, PlanItemAlreadyBriefedError, ProvenanceMismatchError
from app.planning.models import ContentPlan
from app.planning.repository import ContentPlanRepository
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository
from tests.contenttest import build_plan_with_item, default_piece_fields, default_version_payload, make_user

pytestmark = pytest.mark.postgres


def _record_brief(session, content_campaign, **overrides) -> ContentBrief:
    _campaign, _run, _stages, plan, item = content_campaign
    return ContentService(session).record_brief(plan_item=item, content_plan=plan, brief="Produce a beginner reel.", **overrides)


def _record_piece(session, brief: ContentBrief, **overrides) -> tuple[ContentPiece, ContentVersion]:
    fields = default_piece_fields()
    fields.update(overrides)
    return ContentService(session).record_piece(content_brief=brief, initial_payload=default_version_payload(), **fields)


# --- Content Brief: domain persistence -----------------------------------


def test_content_brief_persists_with_correct_public_id_and_workspace(content_campaign, db_session) -> None:
    campaign, _run, _stages, plan, _item = content_campaign
    brief = _record_brief(db_session, content_campaign)
    assert brief.public_id.startswith("CBRF-")
    assert brief.workspace_id == campaign.workspace_id
    assert brief.content_plan_id == plan.id


def test_content_brief_ancestry_mismatch_is_rejected(db_session) -> None:
    _c1, _r1, _s1, plan_a, item_a = build_plan_with_item(db_session, campaign_name="A")
    _c2, _r2, _s2, plan_b, _item_b = build_plan_with_item(db_session, campaign_name="B")

    with pytest.raises(ProvenanceMismatchError):
        ContentService(db_session).record_brief(plan_item=item_a, content_plan=plan_b, brief="Should be rejected.")


def test_duplicate_content_brief_for_same_plan_item_is_rejected(content_campaign, db_session) -> None:
    _record_brief(db_session, content_campaign)
    with pytest.raises(PlanItemAlreadyBriefedError):
        _record_brief(db_session, content_campaign)


def test_content_brief_workspace_mismatch_with_content_plan_rejected_at_db_level(db_session) -> None:
    """Bypasses the service entirely — direct model construction proves
    the composite FK itself rejects a workspace mismatch."""
    _campaign, _run, _stages, plan, item = build_plan_with_item(db_session)
    other_workspace = WorkspaceRepository(db_session).create(
        organization_id=OrganizationRepository(db_session).create(name="Rogue Org").id, name="Rogue WS"
    )
    db_session.flush()

    rogue = ContentBrief(
        public_id="CBRF-MISMATCHTEST", workspace_id=other_workspace.id, plan_item_id=item.id, content_plan_id=plan.id,
        brief="Should be rejected at the DB level.",
    )
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_no_forbidden_fields_exist_on_content_brief() -> None:
    forbidden = (
        "campaign_id", "campaign_run_id", "stage_execution_id", "status", "handed_off", "approved",
        "version", "updated_at", "strategy_id", "hypothesis_id", "experiment_id", "agent_id",
    )
    columns = [c.lower() for c in ContentBrief.__table__.columns.keys()]
    for term in forbidden:
        assert not any(term in c for c in columns), f"unexpected field containing {term!r} on ContentBrief"


# --- Content Piece: domain persistence ------------------------------------


def test_content_piece_and_initial_version_persist_atomically(content_campaign, db_session) -> None:
    brief = _record_brief(db_session, content_campaign)
    piece, version = _record_piece(db_session, brief)
    assert piece.public_id.startswith("CNT-")
    assert piece.workspace_id == brief.workspace_id
    assert piece.status is ContentPieceStatus.DRAFT
    assert version.public_id.startswith("CNV-")
    assert version.content_piece_id == piece.id


def test_content_piece_workspace_mismatch_with_brief_rejected_at_db_level(content_campaign, db_session) -> None:
    brief = _record_brief(db_session, content_campaign)
    other_workspace = WorkspaceRepository(db_session).create(
        organization_id=OrganizationRepository(db_session).create(name="Rogue Org 2").id, name="Rogue WS 2"
    )
    db_session.flush()
    fields = default_piece_fields()
    rogue = ContentPiece(public_id="CNT-MISMATCHTEST", workspace_id=other_workspace.id, content_brief_id=brief.id, **fields)
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_no_forbidden_fields_exist_on_content_piece() -> None:
    forbidden = (
        "plan_item_id", "content_plan_id", "campaign_run_id", "stage_execution_id", "strategy_id",
        "hypothesis_id", "experiment_id", "variant_id", "distribution_id", "paid_media_id", "body",
        "payload", "copy", "script", "approval_id", "agent_id",
    )
    columns = [c.lower() for c in ContentPiece.__table__.columns.keys()]
    for term in forbidden:
        assert not any(term in c for c in columns), f"unexpected field containing {term!r} on ContentPiece"


def test_content_piece_status_enum_membership_is_exact() -> None:
    assert {s.value for s in ContentPieceStatus} == {
        "DRAFT", "IN_PRODUCTION", "PRODUCED", "READY_FOR_REVIEW", "REVISION_REQUESTED",
        "APPROVED", "READY_FOR_DISTRIBUTION", "DISTRIBUTED", "ARCHIVED",
    }


# --- Content Version: no ordinal, via-parent tenancy ----------------------


def test_content_version_has_no_workspace_id_or_ordinal_column() -> None:
    columns = [c.lower() for c in ContentVersion.__table__.columns.keys()]
    assert "workspace_id" not in columns
    for term in ("version", "ordinal", "sequence"):
        assert not any(term in c for c in columns), f"unexpected field containing {term!r} on ContentVersion"


def test_content_version_cannot_reference_nonexistent_piece(db_session) -> None:
    rogue = ContentVersion(public_id="CNV-ORPHANTEST", content_piece_id=uuid.uuid4(), payload={"kind": "reel"})
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_multiple_versions_persist_and_latest_is_selected_by_created_at_then_id(content_campaign, db_session) -> None:
    brief = _record_brief(db_session, content_campaign)
    piece, v1 = _record_piece(db_session, brief)
    service = ContentService(db_session)
    v2 = service.record_version(content_piece=piece, payload=default_version_payload(caption="v2"))
    v3 = service.record_version(content_piece=piece, payload=default_version_payload(caption="v3"))

    _piece, latest = service.get_piece_detail_for_campaign(
        campaign_id=content_campaign[0].id, content_piece_public_id=piece.public_id
    )
    assert latest.id == v3.id
    assert v1.id != v2.id != v3.id


def test_record_version_does_not_mutate_piece_status(content_campaign, db_session) -> None:
    brief = _record_brief(db_session, content_campaign)
    piece, _v1 = _record_piece(db_session, brief)
    ContentService(db_session).record_version(content_piece=piece, payload=default_version_payload())
    db_session.refresh(piece)
    assert piece.status is ContentPieceStatus.DRAFT


# --- Content Approval: domain persistence ---------------------------------


def test_content_approval_has_no_workspace_id_column() -> None:
    assert "workspace_id" not in [c.lower() for c in ContentApproval.__table__.columns.keys()]


def test_content_approval_cannot_reference_nonexistent_version(db_session) -> None:
    rogue = ContentApproval(public_id="APR-ORPHANTEST", content_version_id=uuid.uuid4())
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_content_approval_status_enum_membership_is_exact() -> None:
    assert {s.value for s in ContentApprovalStatus} == {
        "REQUESTED", "UNDER_REVIEW", "APPROVED", "CHANGES_REQUESTED", "REJECTED", "EXPIRED",
    }


def test_request_approval_creates_requested_status(content_campaign, db_session) -> None:
    brief = _record_brief(db_session, content_campaign)
    _piece, version = _record_piece(db_session, brief)
    approval = ContentService(db_session).request_approval(content_version=version)
    assert approval.public_id.startswith("APR-")
    assert approval.status is ContentApprovalStatus.REQUESTED
    assert approval.content_version_id == version.id


# --- state machine: legal graph is complete, independent of exposure -----


def test_content_piece_transition_graph_matches_canonical_state_machine_d() -> None:
    S = ContentPieceStatus
    assert CONTENT_PIECE_TRANSITIONS[S.DRAFT] == frozenset({S.IN_PRODUCTION})
    assert CONTENT_PIECE_TRANSITIONS[S.IN_PRODUCTION] == frozenset({S.PRODUCED})
    assert CONTENT_PIECE_TRANSITIONS[S.PRODUCED] == frozenset({S.READY_FOR_REVIEW})
    assert CONTENT_PIECE_TRANSITIONS[S.READY_FOR_REVIEW] == frozenset({S.REVISION_REQUESTED, S.APPROVED, S.ARCHIVED})
    assert CONTENT_PIECE_TRANSITIONS[S.REVISION_REQUESTED] == frozenset({S.IN_PRODUCTION})
    assert CONTENT_PIECE_TRANSITIONS[S.APPROVED] == frozenset({S.READY_FOR_DISTRIBUTION, S.ARCHIVED})
    assert CONTENT_PIECE_TRANSITIONS[S.READY_FOR_DISTRIBUTION] == frozenset({S.DISTRIBUTED})
    assert CONTENT_PIECE_TRANSITIONS[S.DISTRIBUTED] == frozenset()
    assert CONTENT_PIECE_TRANSITIONS[S.ARCHIVED] == frozenset()


def test_content_approval_transition_graph_matches_canonical_state_machine_e() -> None:
    S = ContentApprovalStatus
    assert CONTENT_APPROVAL_TRANSITIONS[S.REQUESTED] == frozenset({S.UNDER_REVIEW, S.EXPIRED})
    assert CONTENT_APPROVAL_TRANSITIONS[S.UNDER_REVIEW] == frozenset(
        {S.APPROVED, S.CHANGES_REQUESTED, S.REJECTED, S.EXPIRED}
    )
    for terminal in (S.APPROVED, S.CHANGES_REQUESTED, S.REJECTED, S.EXPIRED):
        assert CONTENT_APPROVAL_TRANSITIONS[terminal] == frozenset()


def test_illegal_content_piece_transitions_are_rejected_by_the_matrix() -> None:
    assert not is_legal_content_piece_transition(ContentPieceStatus.DRAFT, ContentPieceStatus.APPROVED)
    assert not is_legal_content_piece_transition(ContentPieceStatus.DISTRIBUTED, ContentPieceStatus.IN_PRODUCTION)
    assert not is_legal_content_piece_transition(ContentPieceStatus.ARCHIVED, ContentPieceStatus.DRAFT)


def test_illegal_content_approval_transitions_are_rejected_by_the_matrix() -> None:
    assert not is_legal_content_approval_transition(ContentApprovalStatus.REQUESTED, ContentApprovalStatus.APPROVED)
    assert not is_legal_content_approval_transition(ContentApprovalStatus.APPROVED, ContentApprovalStatus.REJECTED)


# --- bookkeeping transition service methods -------------------------------


def test_mark_in_production_legal_from_draft(content_campaign, db_session) -> None:
    campaign, _run, _stages, _plan, _item = content_campaign
    brief = _record_brief(db_session, content_campaign)
    piece, _v = _record_piece(db_session, brief)
    updated = ContentService(db_session).mark_in_production(
        workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id
    )
    assert updated.status is ContentPieceStatus.IN_PRODUCTION


def test_full_bookkeeping_chain_to_ready_for_review(content_campaign, db_session) -> None:
    campaign, _run, _stages, _plan, _item = content_campaign
    brief = _record_brief(db_session, content_campaign)
    piece, _v = _record_piece(db_session, brief)
    service = ContentService(db_session)
    service.mark_in_production(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
    service.mark_produced(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
    updated = service.mark_ready_for_review(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
    assert updated.status is ContentPieceStatus.READY_FOR_REVIEW


def test_mark_produced_illegal_from_draft_is_rejected(content_campaign, db_session) -> None:
    campaign, _run, _stages, _plan, _item = content_campaign
    brief = _record_brief(db_session, content_campaign)
    piece, _v = _record_piece(db_session, brief)
    with pytest.raises(InvalidLifecycleTransitionError):
        ContentService(db_session).mark_produced(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)


def test_mark_in_production_legal_from_revision_requested(content_campaign, db_session) -> None:
    """Governance Freeze §12: one method covers both DRAFT and
    REVISION_REQUESTED sources, since both target IN_PRODUCTION."""
    campaign, _run, _stages, _plan, _item = content_campaign
    brief = _record_brief(db_session, content_campaign)
    piece, _v = _record_piece(db_session, brief)
    # Force the piece into REVISION_REQUESTED by direct mutation for this
    # narrow test — no code path in this codebase reaches it automatically
    # (Governance Freeze §13/§25).
    piece.status = ContentPieceStatus.REVISION_REQUESTED
    db_session.flush()
    updated = ContentService(db_session).mark_in_production(
        workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id
    )
    assert updated.status is ContentPieceStatus.IN_PRODUCTION


def test_transition_of_unknown_piece_is_forbidden(content_campaign, db_session) -> None:
    campaign, *_ = content_campaign
    with pytest.raises(ForbiddenError):
        ContentService(db_session).mark_in_production(workspace_id=campaign.workspace_id, content_piece_public_id="CNT-DOESNOTEXIST")


def test_transition_of_another_workspaces_piece_is_forbidden(content_campaign, db_session) -> None:
    campaign, *_ = content_campaign
    brief = _record_brief(db_session, content_campaign)
    piece, _v = _record_piece(db_session, brief)

    _c2, _r2, _s2, plan_b, item_b = build_plan_with_item(db_session, campaign_name="Other Campaign")
    with pytest.raises(ForbiddenError):
        ContentService(db_session).mark_in_production(workspace_id=plan_b.workspace_id, content_piece_public_id=piece.public_id)


# --- archive: exact sources only, no arbitrary state ----------------------


def test_archive_legal_from_ready_for_review(content_campaign, db_session) -> None:
    campaign, *_ = content_campaign
    brief = _record_brief(db_session, content_campaign)
    piece, _v = _record_piece(db_session, brief)
    service = ContentService(db_session)
    service.mark_in_production(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
    service.mark_produced(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
    service.mark_ready_for_review(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
    archived = service.archive_piece(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
    assert archived.status is ContentPieceStatus.ARCHIVED
    assert archived.archived_at is not None


def test_archive_illegal_from_draft(content_campaign, db_session) -> None:
    campaign, *_ = content_campaign
    brief = _record_brief(db_session, content_campaign)
    piece, _v = _record_piece(db_session, brief)
    with pytest.raises(InvalidLifecycleTransitionError):
        ContentService(db_session).archive_piece(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)


# --- governance: no forbidden methods, no forbidden tables ----------------


def test_content_service_exposes_no_decision_making_or_deferred_methods() -> None:
    forbidden_methods = (
        "decide_approval", "resolve_approval",
        "mark_ready_for_distribution", "mark_distributed", "set_ready_for_distribution", "set_distributed",
        "is_content_approver", "approval_role", "governance_role", "agent_00_role",
    )
    for method in forbidden_methods:
        assert not hasattr(ContentService, method), f"unexpected method {method!r} on ContentService"


def test_no_content_revision_request_or_asset_or_distribution_table_was_introduced() -> None:
    from app.persistence.base import metadata

    table_names = set(metadata.tables.keys())
    for forbidden_table in (
        "content_revision_requests", "creative_briefs", "assets", "asset_versions",
        "distribution_plans", "paid_media_plans",
    ):
        assert forbidden_table not in table_names


def test_plan_item_and_content_plan_are_structurally_unchanged() -> None:
    """PlanItem fields/tenancy/mutation-behavior must remain exactly as
    BACKEND-09 froze them — the only authorized Planning change is the
    additive candidate key on ContentPlan (Phase 2 §8/§43)."""
    from app.planning.models import PlanItem

    plan_item_columns = set(PlanItem.__table__.columns.keys())
    assert "workspace_id" not in plan_item_columns
    assert "content_brief_id" not in plan_item_columns
    assert "status" not in plan_item_columns
    assert "briefed" not in plan_item_columns
    assert "locked" not in plan_item_columns

    constraint_names = {c.name for c in ContentPlan.__table__.constraints if getattr(c, "name", None)}
    assert "uq_content_plans_id_workspace_id" in constraint_names


# --- approval review-state bookkeeping ------------------------------------


def test_mark_under_review_legal_from_requested(content_campaign, db_session) -> None:
    campaign, *_ = content_campaign
    brief = _record_brief(db_session, content_campaign)
    _piece, version = _record_piece(db_session, brief)
    service = ContentService(db_session)
    approval = service.request_approval(content_version=version)
    updated = service.mark_under_review(workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id)
    assert updated.status is ContentApprovalStatus.UNDER_REVIEW


def test_mark_under_review_illegal_from_under_review(content_campaign, db_session) -> None:
    campaign, *_ = content_campaign
    brief = _record_brief(db_session, content_campaign)
    _piece, version = _record_piece(db_session, brief)
    service = ContentService(db_session)
    approval = service.request_approval(content_version=version)
    service.mark_under_review(workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id)
    with pytest.raises(InvalidLifecycleTransitionError):
        service.mark_under_review(workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id)


# --- APPROVED coupling: required and atomic -------------------------------


def _bring_piece_to_ready_for_review(service: ContentService, *, workspace_id, piece: ContentPiece) -> None:
    service.mark_in_production(workspace_id=workspace_id, content_piece_public_id=piece.public_id)
    service.mark_produced(workspace_id=workspace_id, content_piece_public_id=piece.public_id)
    service.mark_ready_for_review(workspace_id=workspace_id, content_piece_public_id=piece.public_id)


def test_approved_decision_couples_piece_status_atomically(content_campaign, db_session) -> None:
    campaign, *_ = content_campaign
    brief = _record_brief(db_session, content_campaign)
    piece, version = _record_piece(db_session, brief)
    service = ContentService(db_session)
    _bring_piece_to_ready_for_review(service, workspace_id=campaign.workspace_id, piece=piece)
    approval = service.request_approval(content_version=version)
    service.mark_under_review(workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id)
    reviewer = make_user(db_session)

    resolved = service.record_authorized_approval_decision(
        workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id,
        decision=ContentApprovalStatus.APPROVED, actor_user_id=reviewer.id,
    )
    assert resolved.status is ContentApprovalStatus.APPROVED
    assert resolved.reviewer_user_id == reviewer.id
    assert resolved.decided_at is not None

    db_session.refresh(piece)
    assert piece.status is ContentPieceStatus.APPROVED


def test_approved_decision_requires_piece_to_be_ready_for_review(content_campaign, db_session) -> None:
    """A Content Piece stuck at DRAFT (never brought through the
    bookkeeping chain) cannot be coupled to APPROVED — the piece-side
    transition is validated against the real state machine, not assumed."""
    campaign, *_ = content_campaign
    brief = _record_brief(db_session, content_campaign)
    piece, version = _record_piece(db_session, brief)
    service = ContentService(db_session)
    approval = service.request_approval(content_version=version)
    service.mark_under_review(workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id)
    reviewer = make_user(db_session)

    with pytest.raises(InvalidLifecycleTransitionError):
        service.record_authorized_approval_decision(
            workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id,
            decision=ContentApprovalStatus.APPROVED, actor_user_id=reviewer.id,
        )


def test_changes_requested_does_not_mutate_piece_status(content_campaign, db_session) -> None:
    campaign, *_ = content_campaign
    brief = _record_brief(db_session, content_campaign)
    piece, version = _record_piece(db_session, brief)
    service = ContentService(db_session)
    _bring_piece_to_ready_for_review(service, workspace_id=campaign.workspace_id, piece=piece)
    approval = service.request_approval(content_version=version)
    service.mark_under_review(workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id)
    reviewer = make_user(db_session)

    resolved = service.record_authorized_approval_decision(
        workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id,
        decision=ContentApprovalStatus.CHANGES_REQUESTED, actor_user_id=reviewer.id,
    )
    assert resolved.status is ContentApprovalStatus.CHANGES_REQUESTED

    db_session.refresh(piece)
    assert piece.status is ContentPieceStatus.READY_FOR_REVIEW


def test_rejected_does_not_mutate_piece_status(content_campaign, db_session) -> None:
    campaign, *_ = content_campaign
    brief = _record_brief(db_session, content_campaign)
    piece, version = _record_piece(db_session, brief)
    service = ContentService(db_session)
    _bring_piece_to_ready_for_review(service, workspace_id=campaign.workspace_id, piece=piece)
    approval = service.request_approval(content_version=version)
    service.mark_under_review(workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id)
    reviewer = make_user(db_session)

    resolved = service.record_authorized_approval_decision(
        workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id,
        decision=ContentApprovalStatus.REJECTED, actor_user_id=reviewer.id,
    )
    assert resolved.status is ContentApprovalStatus.REJECTED

    db_session.refresh(piece)
    assert piece.status is ContentPieceStatus.READY_FOR_REVIEW, "REJECTED has no canonical Content Piece mapping"


def test_expired_is_not_an_authorized_decision_value(content_campaign, db_session) -> None:
    campaign, *_ = content_campaign
    brief = _record_brief(db_session, content_campaign)
    _piece, version = _record_piece(db_session, brief)
    service = ContentService(db_session)
    approval = service.request_approval(content_version=version)
    service.mark_under_review(workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id)
    reviewer = make_user(db_session)

    with pytest.raises(InvalidLifecycleTransitionError):
        service.record_authorized_approval_decision(
            workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id,
            decision=ContentApprovalStatus.EXPIRED, actor_user_id=reviewer.id,
        )


def test_no_automatic_expiration_mechanism_exists() -> None:
    forbidden_methods = ("expire_approval", "check_expiration", "run_expiration_sweep")
    for method in forbidden_methods:
        assert not hasattr(ContentService, method), f"unexpected method {method!r} on ContentService"


# --- stage-lifecycle non-mutation -----------------------------------------


def test_recording_brief_and_piece_leaves_run_and_stage_unchanged(content_campaign, db_session) -> None:
    from app.campaigns.models import CampaignRunStatus
    from app.orchestration.models import StageExecutionStatus

    campaign, run, stages, _plan, _item = content_campaign
    brief = _record_brief(db_session, content_campaign)
    _record_piece(db_session, brief)
    db_session.refresh(run)
    assert run.status is CampaignRunStatus.CREATED
    for stage in stages.values():
        db_session.refresh(stage)
        assert stage.status is StageExecutionStatus.PENDING
