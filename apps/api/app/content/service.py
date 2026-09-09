"""Content persistence — BACKEND-10.

No public HTTP write endpoint exists anywhere in this module (§28/§29/§30
of the Governance Freeze) — BACKEND-01's own API map marks the two Content
routes GET-only, and the canonical ``POST .../approvals`` route is
explicitly deferred pending an unresolved workspace-authorization-boundary
decision (Phase 1B §N). Every write here is service-layer-only, the same
shape ``StrategyService``/``PlanningService``/``OrchestrationService``
already established: tests and any future, separately-authorized caller
invoke these methods directly.

Two different calling shapes, deliberately:

1. "Creation" methods (``record_brief``, ``record_piece``,
   ``record_version``, ``request_approval``) take **already-loaded parent
   domain objects** — the same shape ``StrategyService.record_strategy``/
   ``PlanningService.record_plan`` already use, trusting that the caller
   (a test, or a future orchestration runtime) already resolved and
   authorized those objects.
2. "Transition" methods (``mark_in_production``, ``mark_produced``,
   ``mark_ready_for_review``, ``archive_piece``, ``mark_under_review``,
   ``record_authorized_approval_decision``) take a ``workspace_id`` plus a
   public id and re-verify tenant ownership themselves before mutating —
   the same defensive shape ``StrategyService.transition_hypothesis``
   already uses for its own mutation of an existing row.

PERSISTING AN APPROVAL DECISION != HAVING AUTHORITY TO MAKE THAT DECISION.
``record_authorized_approval_decision`` never decides *whether* the caller
was allowed to approve — it persists a decision the caller has already had
authorized elsewhere (a human reviewer, per BACKEND-01's own explicit
human-in-the-loop MVP path), the same trust boundary
``HumanDecisionResponse``/``transition_hypothesis`` already establish.
WORKSPACE ROLE != CONTENT APPROVAL AUTHORITY — nothing here calls
``require_role`` or checks any role/permission as a substitute for content
governance authority.

CONTENT VERSION CREATED != APPROVAL GRANTED. APPROVED is set **only** by
``record_authorized_approval_decision`` when the decision is ``APPROVED``,
coupled atomically with the underlying Content Piece's own transition — no
other code path in this module ever sets ``ContentPiece.status =
APPROVED``. ``CHANGES_REQUESTED`` and ``REJECTED`` persist the Approval
decision only and never mutate Content Piece status (Phase 1B §O: the
``CHANGES_REQUESTED -> REVISION_REQUESTED`` coupling is inferred, not
canonically proven, and ``REJECTED`` has no Content Piece mapping at all —
both remain carried-forward reservations, not implemented here).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.content.models import ContentApproval, ContentApprovalStatus, ContentBrief, ContentPiece, ContentPieceStatus, ContentVersion
from app.content.repository import (
    ContentApprovalRepository,
    ContentBriefRepository,
    ContentPieceRepository,
    ContentVersionRepository,
)
from app.content.transitions import is_legal_content_approval_transition, is_legal_content_piece_transition
from app.core.api_errors import ForbiddenError, InvalidLifecycleTransitionError, PlanItemAlreadyBriefedError, ProvenanceMismatchError
from app.planning.models import ContentPlan, PlanItem

EVENT_BRIEF_RECORDED = "content.brief.recorded"
EVENT_PIECE_RECORDED = "content.piece.recorded"
EVENT_VERSION_RECORDED = "content.version.recorded"
EVENT_PIECE_STATUS_CHANGED = "content.piece.status_changed"
EVENT_APPROVAL_RECORDED = "content.approval.recorded"
EVENT_APPROVAL_STATUS_CHANGED = "content.approval.status_changed"
EVENT_APPROVAL_DECISION_RECORDED = "content.approval.decision_recorded"

# The only terminal decisions record_authorized_approval_decision accepts.
# EXPIRED is deliberately excluded — no automatic/manual expiry trigger is
# authorized in this stage (§27 of the Governance Freeze).
_AUTHORIZED_APPROVAL_DECISIONS = frozenset(
    {ContentApprovalStatus.APPROVED, ContentApprovalStatus.CHANGES_REQUESTED, ContentApprovalStatus.REJECTED}
)


class ContentService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.briefs = ContentBriefRepository(session)
        self.pieces = ContentPieceRepository(session)
        self.versions = ContentVersionRepository(session)
        self.approvals = ContentApprovalRepository(session)
        self.events = AuditEventRepository(session)

    # --- reads (GET-only public surface calls these) -----------------

    def list_pieces_for_campaign(self, *, campaign_id: uuid.UUID) -> list[ContentPiece]:
        return self.pieces.list_for_campaign(campaign_id)

    def get_piece_detail_for_campaign(
        self, *, campaign_id: uuid.UUID, content_piece_public_id: str
    ) -> tuple[ContentPiece, ContentVersion | None] | None:
        piece = self.pieces.get_for_campaign_by_public_id(campaign_id=campaign_id, public_id=content_piece_public_id)
        if piece is None:
            return None
        latest_version = self.versions.get_latest_for_piece(piece.id)
        return piece, latest_version

    # --- Content Brief: service-layer only, no public route ----------

    def record_brief(
        self,
        *,
        plan_item: PlanItem,
        content_plan: ContentPlan,
        brief: str,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> ContentBrief:
        """CONTENT BRIEF PROVENANCE = DERIVED VIA PLANNING ANCESTRY (Phase
        1B §C) — no campaign_run_id/stage_execution_id check happens here;
        the only provenance guarantee this stage makes is that the given
        Plan Item genuinely belongs to the given Content Plan. A plain FK
        alone cannot prove that — checked explicitly, same discipline as
        every other provenance check in this codebase."""
        if plan_item.content_plan_id != content_plan.id:
            raise ProvenanceMismatchError()

        try:
            brief_row = self.briefs.create(plan_item=plan_item, content_plan=content_plan, brief=brief)
        except IntegrityError:
            self.session.rollback()
            raise PlanItemAlreadyBriefedError() from None

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=content_plan.workspace_id,
            event_type=EVENT_BRIEF_RECORDED,
            actor_type=actor_type,
            content_plan_id=content_plan.id,
            plan_item_id=plan_item.id,
            content_brief_id=brief_row.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return brief_row

    # --- Content Piece + initial Content Version: atomic --------------

    def record_piece(
        self,
        *,
        content_brief: ContentBrief,
        format: str,
        objective: str,
        funnel_stage: str,
        cta: str,
        channel: str,
        initial_payload: dict,
        created_by_user_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> tuple[ContentPiece, ContentVersion]:
        """Atomically creates ContentPiece + its initial ContentVersion +
        one AuditEvent per created entity. A Piece is never left without
        at least one Version from this path — if anything raises before
        the final commit, nothing persists."""
        piece = self.pieces.create(
            content_brief=content_brief, format=format, objective=objective,
            funnel_stage=funnel_stage, cta=cta, channel=channel,
        )
        version = self.versions.create(content_piece=piece, payload=initial_payload, created_by_user_id=created_by_user_id)

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=content_brief.workspace_id,
            event_type=EVENT_PIECE_RECORDED,
            actor_type=actor_type,
            content_brief_id=content_brief.id,
            content_piece_id=piece.id,
            new_state=piece.status.value,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.events.record(
            workspace_id=content_brief.workspace_id,
            event_type=EVENT_VERSION_RECORDED,
            actor_type=actor_type,
            content_piece_id=piece.id,
            content_version_id=version.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return piece, version

    # --- additional Content Version: service-layer only ---------------

    def record_version(
        self,
        *,
        content_piece: ContentPiece,
        payload: dict,
        created_by_user_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> ContentVersion:
        """CONTENT VERSION CREATED != CONTENT APPROVED — this method never
        touches ContentPiece.status, ContentApproval, or any orchestration
        table."""
        version = self.versions.create(content_piece=content_piece, payload=payload, created_by_user_id=created_by_user_id)
        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=content_piece.workspace_id,
            event_type=EVENT_VERSION_RECORDED,
            actor_type=actor_type,
            content_piece_id=content_piece.id,
            content_version_id=version.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return version

    # --- Content Piece bookkeeping transitions -------------------------
    # Pure event-recording, no governance authority required — mirrors
    # RunStageExecution's own "persisted workflow structure, not AI
    # execution" precedent exactly.

    def _load_piece_for_transition(self, *, workspace_id: uuid.UUID, content_piece_public_id: str) -> ContentPiece:
        piece = self.pieces.get_by_public_id(content_piece_public_id, for_update=True)
        if piece is None or piece.workspace_id != workspace_id:
            raise ForbiddenError()
        return piece

    def _apply_piece_transition(
        self,
        *,
        piece: ContentPiece,
        target: ContentPieceStatus,
        actor_user_id: uuid.UUID | None,
        request_id: str | None,
    ) -> ContentPiece:
        if not is_legal_content_piece_transition(piece.status, target):
            raise InvalidLifecycleTransitionError(
                f"Cannot transition a content piece from {piece.status.value} to {target.value}."
            )
        previous = piece.status
        piece.status = target
        self.events.record(
            workspace_id=piece.workspace_id,
            event_type=EVENT_PIECE_STATUS_CHANGED,
            actor_type=ActorType.USER if actor_user_id is not None else ActorType.SYSTEM,
            content_piece_id=piece.id,
            previous_state=previous.value,
            new_state=target.value,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return piece

    def mark_in_production(
        self, *, workspace_id: uuid.UUID, content_piece_public_id: str,
        actor_user_id: uuid.UUID | None = None, request_id: str | None = None,
    ) -> ContentPiece:
        """Legal from DRAFT (start of production) or REVISION_REQUESTED
        (resuming after a revision) — both edges target IN_PRODUCTION, so
        one method covers both rather than inventing two near-identical
        names for the same target state."""
        piece = self._load_piece_for_transition(workspace_id=workspace_id, content_piece_public_id=content_piece_public_id)
        return self._apply_piece_transition(
            piece=piece, target=ContentPieceStatus.IN_PRODUCTION, actor_user_id=actor_user_id, request_id=request_id
        )

    def mark_produced(
        self, *, workspace_id: uuid.UUID, content_piece_public_id: str,
        actor_user_id: uuid.UUID | None = None, request_id: str | None = None,
    ) -> ContentPiece:
        piece = self._load_piece_for_transition(workspace_id=workspace_id, content_piece_public_id=content_piece_public_id)
        return self._apply_piece_transition(
            piece=piece, target=ContentPieceStatus.PRODUCED, actor_user_id=actor_user_id, request_id=request_id
        )

    def mark_ready_for_review(
        self, *, workspace_id: uuid.UUID, content_piece_public_id: str,
        actor_user_id: uuid.UUID | None = None, request_id: str | None = None,
    ) -> ContentPiece:
        piece = self._load_piece_for_transition(workspace_id=workspace_id, content_piece_public_id=content_piece_public_id)
        return self._apply_piece_transition(
            piece=piece, target=ContentPieceStatus.READY_FOR_REVIEW, actor_user_id=actor_user_id, request_id=request_id
        )

    def archive_piece(
        self, *, workspace_id: uuid.UUID, content_piece_public_id: str,
        actor_user_id: uuid.UUID | None = None, request_id: str | None = None,
    ) -> ContentPiece:
        """Legal only from READY_FOR_REVIEW or APPROVED (state machine D)
        — never a blanket "archive from anywhere" method. Mirrors
        ``CampaignService.archive_campaign``'s administrative, non-
        governance nature: this is housekeeping, not a content-approval
        decision."""
        piece = self._load_piece_for_transition(workspace_id=workspace_id, content_piece_public_id=content_piece_public_id)
        piece = self._apply_piece_transition(
            piece=piece, target=ContentPieceStatus.ARCHIVED, actor_user_id=actor_user_id, request_id=request_id
        )
        piece.archived_at = datetime.now(timezone.utc)
        self.session.commit()
        return piece

    # --- Content Approval: request / review-state bookkeeping ----------

    def request_approval(
        self, *, content_version: ContentVersion, actor_user_id: uuid.UUID | None = None, request_id: str | None = None
    ) -> ContentApproval:
        """Creates ContentApproval(status=REQUESTED). No governance
        decision is made here — this only opens a request, the same way
        ``OrchestrationService.create_decision_request`` opens a Human
        Decision Request without itself resolving it."""
        approval = self.approvals.create(content_version=content_version)
        piece = self.pieces.get_by_id(content_version.content_piece_id)
        self.events.record(
            workspace_id=piece.workspace_id,
            event_type=EVENT_APPROVAL_RECORDED,
            actor_type=ActorType.USER if actor_user_id is not None else ActorType.SYSTEM,
            content_piece_id=content_version.content_piece_id,
            content_version_id=content_version.id,
            content_approval_id=approval.id,
            new_state=approval.status.value,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return approval

    def _load_approval_and_piece_for_transition(
        self, *, workspace_id: uuid.UUID, content_approval_public_id: str
    ) -> tuple[ContentApproval, ContentPiece]:
        """Row-locks both the Approval and its owning Piece — the coupled
        APPROVED transition (§ below) mutates both in one transaction, so
        both must be locked against a concurrent second decision racing
        on the same Approval/Piece pair."""
        approval = self.approvals.get_by_public_id(content_approval_public_id, for_update=True)
        if approval is None:
            raise ForbiddenError()
        version = self.versions.get_by_id(approval.content_version_id)
        if version is None:
            raise ForbiddenError()
        piece = self.pieces.get_by_id(version.content_piece_id, for_update=True)
        if piece is None or piece.workspace_id != workspace_id:
            raise ForbiddenError()
        return approval, piece

    def mark_under_review(
        self, *, workspace_id: uuid.UUID, content_approval_public_id: str,
        actor_user_id: uuid.UUID | None = None, request_id: str | None = None,
    ) -> ContentApproval:
        """Only legal transition: REQUESTED -> UNDER_REVIEW. Pure
        bookkeeping ("someone started looking at it") — no governance
        outcome is decided."""
        approval, piece = self._load_approval_and_piece_for_transition(
            workspace_id=workspace_id, content_approval_public_id=content_approval_public_id
        )
        target = ContentApprovalStatus.UNDER_REVIEW
        if not is_legal_content_approval_transition(approval.status, target):
            raise InvalidLifecycleTransitionError(
                f"Cannot transition a content approval from {approval.status.value} to {target.value}."
            )
        previous = approval.status
        approval.status = target
        self.events.record(
            workspace_id=piece.workspace_id,
            event_type=EVENT_APPROVAL_STATUS_CHANGED,
            actor_type=ActorType.USER if actor_user_id is not None else ActorType.SYSTEM,
            content_piece_id=piece.id,
            content_approval_id=approval.id,
            previous_state=previous.value,
            new_state=target.value,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return approval

    # --- Content Approval: authorized decision recording ---------------

    def record_authorized_approval_decision(
        self,
        *,
        workspace_id: uuid.UUID,
        content_approval_public_id: str,
        decision: ContentApprovalStatus,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> ContentApproval:
        """Persists a decision the CALLER has already had authorized
        elsewhere (a human reviewer, per BACKEND-01's own explicit
        human-in-the-loop MVP path) — this method never itself determines
        whether ``actor_user_id`` was allowed to approve. ``actor_user_id``
        is required (not optional) here specifically because BACKEND-01's
        own text requires recording *that it was a human* who decided.

        Only APPROVED, CHANGES_REQUESTED, REJECTED are accepted — EXPIRED
        has no authorized trigger in this stage (§27).

        APPROVED is the only decision coupled, atomically, with a
        ContentPiece status transition (READY_FOR_REVIEW -> APPROVED),
        per the architecture doc's own explicit transaction-boundary rule.
        CHANGES_REQUESTED and REJECTED persist the Approval decision only
        — neither touches ContentPiece.status (Phase 1B §O: the
        CHANGES_REQUESTED coupling is inferred, not proven; REJECTED has
        no canonical Content Piece mapping at all)."""
        if decision not in _AUTHORIZED_APPROVAL_DECISIONS:
            raise InvalidLifecycleTransitionError(
                f"{decision.value} is not an authorized decision value for this operation."
            )

        approval, piece = self._load_approval_and_piece_for_transition(
            workspace_id=workspace_id, content_approval_public_id=content_approval_public_id
        )
        if not is_legal_content_approval_transition(approval.status, decision):
            raise InvalidLifecycleTransitionError(
                f"Cannot transition a content approval from {approval.status.value} to {decision.value}."
            )

        previous_approval_status = approval.status
        approval.status = decision
        approval.reviewer_user_id = actor_user_id
        approval.decided_at = datetime.now(timezone.utc)

        self.events.record(
            workspace_id=piece.workspace_id,
            event_type=EVENT_APPROVAL_DECISION_RECORDED,
            actor_type=ActorType.USER,
            content_piece_id=piece.id,
            content_approval_id=approval.id,
            previous_state=previous_approval_status.value,
            new_state=decision.value,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )

        if decision is ContentApprovalStatus.APPROVED:
            # ContentPiece.status = APPROVED must never be produced
            # without this corresponding governance record — one
            # transaction, both writes or neither.
            piece_target = ContentPieceStatus.APPROVED
            if not is_legal_content_piece_transition(piece.status, piece_target):
                raise InvalidLifecycleTransitionError(
                    f"Cannot transition a content piece from {piece.status.value} to {piece_target.value}."
                )
            previous_piece_status = piece.status
            piece.status = piece_target
            self.events.record(
                workspace_id=piece.workspace_id,
                event_type=EVENT_PIECE_STATUS_CHANGED,
                actor_type=ActorType.USER,
                content_piece_id=piece.id,
                content_approval_id=approval.id,
                previous_state=previous_piece_status.value,
                new_state=piece_target.value,
                actor_user_id=actor_user_id,
                request_id=request_id,
            )
        # CHANGES_REQUESTED / REJECTED: no ContentPiece mutation (§O).

        self.session.commit()
        return approval
