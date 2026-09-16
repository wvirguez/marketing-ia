"""Content persistence — BACKEND-10, extended by MVP-20 (Content
Versioning & Approval Revision Loop).

No public HTTP write endpoint exists for Content Brief (§28/§29/§30 of the
Governance Freeze) — BACKEND-01's own API map marks that route deferred.
Every Brief write here is service-layer-only, the same shape
``StrategyService``/``PlanningService``/``OrchestrationService`` already
established: tests and any future, separately-authorized caller invoke
these methods directly. Lifecycle/Approval/revision writes are reachable
through the real ``app/content/router.py`` HTTP surface (MVP-17B, extended
by MVP-20).

Two different calling shapes, deliberately:

1. "Creation" methods (``record_brief``, ``record_piece``,
   ``record_version``) take **already-loaded parent domain objects** — the
   same shape ``StrategyService.record_strategy``/
   ``PlanningService.record_plan`` already use, trusting that the caller
   (a test, or a future orchestration runtime) already resolved and
   authorized those objects.
2. "Transition" methods (``mark_in_production``, ``create_revision_version``,
   ``mark_produced``, ``mark_ready_for_review``, ``archive_piece``,
   ``mark_under_review``, ``request_approval``,
   ``record_authorized_approval_decision``) take a ``workspace_id`` plus a
   public id and re-verify tenant ownership themselves before mutating —
   the same defensive shape ``StrategyService.transition_hypothesis``
   already uses for its own mutation of an existing row. ``request_approval``
   was reclassified into this shape by MVP-20A-R1 (CONTENT-P3-4/
   CONTENT-P3-5): it resolves the current ContentVersion and re-checks the
   Piece's status itself, under its own Piece lock — never trusting a
   value any caller resolved beforehand.

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
APPROVED``. ``CHANGES_REQUESTED`` is likewise coupled atomically (MVP-20):
``ContentPiece.status -> REVISION_REQUESTED``, so the Piece cannot be
resubmitted for approval until a genuine revision occurs. ``REJECTED``
remains a deliberate, out-of-scope exception (MVP-20A §J/MVP-20A-R1 §17):
it still persists the Approval decision only and never mutates Content
Piece status — an accepted, pre-existing semantic boundary, not something
MVP-20 repairs.

REVISION_REQUESTED -> IN_PRODUCTION HAS EXACTLY ONE AUTHORIZED COMMAND
(MVP-20A-R1, CONTENT-P0-6): ``create_revision_version``. The generic
``mark_in_production`` is legal only from ``DRAFT`` — it deliberately does
NOT accept ``REVISION_REQUESTED`` as a source, even though that edge
remains legal in ``app/content/transitions.py`` (state-machine legality is
not the same as command-level authority). This guarantees a ContentPiece
can never return to ``READY_FOR_REVIEW`` after ``CHANGES_REQUESTED``
without a fresh, immutable ``ContentVersion`` having been created first.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.content.models import ContentApproval, ContentApprovalStatus, ContentBrief, ContentDistribution, ContentDistributionStatus, ContentDistributionTrackingRequirement, ContentPiece, ContentPieceStatus, ContentVersion
from app.content.repository import (
    ContentApprovalRepository,
    ContentBriefRepository,
    ContentDistributionRepository,
    ContentDistributionTrackingRequirementRepository,
    ContentPieceRepository,
    ContentVersionRepository,
)
from app.content.transitions import is_legal_content_approval_transition, is_legal_content_piece_transition
from app.core.api_errors import (
    ContentApprovalAlreadyOpenError,
    ForbiddenError,
    InvalidLifecycleTransitionError,
    PlanItemAlreadyBriefedError,
    ProvenanceMismatchError,
    TrackingRequirementAlreadyAssociatedError,
    TrackingRequirementNotAssociatedError,
)
from app.planning.models import ContentPlan, PlanItem
from app.tracking.models import TrackingRequirement

EVENT_BRIEF_RECORDED = "content.brief.recorded"
EVENT_PIECE_RECORDED = "content.piece.recorded"
EVENT_VERSION_RECORDED = "content.version.recorded"
EVENT_PIECE_STATUS_CHANGED = "content.piece.status_changed"
EVENT_APPROVAL_RECORDED = "content.approval.recorded"
EVENT_APPROVAL_STATUS_CHANGED = "content.approval.status_changed"
EVENT_APPROVAL_DECISION_RECORDED = "content.approval.decision_recorded"
EVENT_DISTRIBUTION_READY_RECORDED = "content.distribution.ready_recorded"
EVENT_DISTRIBUTION_RECORDED = "content.distribution.recorded"
EVENT_TRACKING_REQUIREMENT_ASSOCIATED = "distribution.tracking_requirement.associated"
EVENT_TRACKING_REQUIREMENT_DISSOCIATED = "distribution.tracking_requirement.dissociated"

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
        self.distributions = ContentDistributionRepository(session)
        self.distribution_tracking_requirements = ContentDistributionTrackingRequirementRepository(session)
        self.events = AuditEventRepository(session)

    # --- reads (GET-only public surface calls these) -----------------

    def list_pieces_for_campaign(self, *, campaign_id: uuid.UUID) -> list[ContentPiece]:
        return self.pieces.list_for_campaign(campaign_id)

    def get_piece_for_campaign(self, *, campaign_id: uuid.UUID, content_piece_public_id: str) -> ContentPiece | None:
        """Authorize Piece scope without resolving any ContentVersion."""
        return self.pieces.get_for_campaign_by_public_id(campaign_id=campaign_id, public_id=content_piece_public_id)

    def get_piece_detail_for_campaign(
        self, *, campaign_id: uuid.UUID, content_piece_public_id: str
    ) -> tuple[ContentPiece, ContentVersion | None] | None:
        piece = self.pieces.get_for_campaign_by_public_id(campaign_id=campaign_id, public_id=content_piece_public_id)
        if piece is None:
            return None
        latest_version = self.versions.get_latest_for_piece(piece.id)
        return piece, latest_version

    # --- MVP-05E: narrow read helpers for the orchestration bootstrap's
    # own idempotency checks (one Brief per PlanItem, one Piece per Brief)
    # — kept here rather than having orchestration reach into
    # self.briefs/self.pieces/self.versions directly, matching the
    # "callers use a named service method, not a repository" shape every
    # other domain service (Strategy/Planning/Research) already follows. --

    def get_brief_for_plan_item(self, plan_item_id: uuid.UUID) -> ContentBrief | None:
        return self.briefs.get_for_plan_item(plan_item_id)

    def get_piece_for_brief(self, content_brief_id: uuid.UUID) -> ContentPiece | None:
        return self.pieces.get_for_brief(content_brief_id)

    def get_latest_version_for_piece(self, content_piece_id: uuid.UUID) -> ContentVersion | None:
        return self.versions.get_latest_for_piece(content_piece_id)

    def get_distribution_for_piece(self, content_piece_id: uuid.UUID) -> ContentDistribution | None:
        return self.distributions.get_for_piece(content_piece_id)

    def mark_ready_for_distribution(
        self, *, workspace_id: uuid.UUID, content_piece_public_id: str,
        actor_user_id: uuid.UUID, request_id: str | None = None,
    ) -> ContentPiece:
        piece = self._load_piece_for_transition(workspace_id=workspace_id, content_piece_public_id=content_piece_public_id)
        if piece.status is not ContentPieceStatus.APPROVED or self.distributions.get_for_piece(piece.id) is not None:
            raise InvalidLifecycleTransitionError()
        approval = self.approvals.get_approved_for_piece(piece.id)
        if approval is None or approval.content_version_id is None:
            raise ProvenanceMismatchError("The approved ContentVersion provenance is missing or inconsistent.")
        try:
            distribution = self.distributions.create(piece=piece, approval=approval)
            piece.status = ContentPieceStatus.READY_FOR_DISTRIBUTION
            self.events.record(
                workspace_id=piece.workspace_id, event_type=EVENT_DISTRIBUTION_READY_RECORDED,
                actor_type=ActorType.USER, content_piece_id=piece.id,
                content_version_id=approval.content_version_id, content_approval_id=approval.id,
                distribution_id=distribution.id, new_state=ContentDistributionStatus.READY.value,
                actor_user_id=actor_user_id, request_id=request_id,
            )
            self.events.record(
                workspace_id=piece.workspace_id, event_type=EVENT_PIECE_STATUS_CHANGED,
                actor_type=ActorType.USER, content_piece_id=piece.id,
                content_approval_id=approval.id, distribution_id=distribution.id,
                previous_state=ContentPieceStatus.APPROVED.value,
                new_state=piece.status.value, actor_user_id=actor_user_id, request_id=request_id,
            )
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise InvalidLifecycleTransitionError() from None
        return piece

    def record_distributed(
        self, *, workspace_id: uuid.UUID, content_piece_public_id: str,
        actor_user_id: uuid.UUID, external_reference: str | None = None,
        request_id: str | None = None,
    ) -> ContentPiece:
        piece = self._load_piece_for_transition(workspace_id=workspace_id, content_piece_public_id=content_piece_public_id)
        distribution = self.distributions.get_for_piece(piece.id)
        if (piece.status is not ContentPieceStatus.READY_FOR_DISTRIBUTION or distribution is None
                or distribution.status is not ContentDistributionStatus.READY):
            raise InvalidLifecycleTransitionError()
        distribution.status = ContentDistributionStatus.DISTRIBUTED
        distribution.external_reference = external_reference
        distribution.distributed_at = datetime.now(timezone.utc)
        piece.status = ContentPieceStatus.DISTRIBUTED
        self.events.record(
            workspace_id=piece.workspace_id, event_type=EVENT_DISTRIBUTION_RECORDED,
            actor_type=ActorType.USER, content_piece_id=piece.id,
            content_version_id=distribution.content_version_id, distribution_id=distribution.id,
            previous_state=ContentDistributionStatus.READY.value,
            new_state=ContentDistributionStatus.DISTRIBUTED.value,
            actor_user_id=actor_user_id, request_id=request_id,
        )
        self.events.record(
            workspace_id=piece.workspace_id, event_type=EVENT_PIECE_STATUS_CHANGED,
            actor_type=ActorType.USER, content_piece_id=piece.id, distribution_id=distribution.id,
            previous_state=ContentPieceStatus.READY_FOR_DISTRIBUTION.value,
            new_state=piece.status.value, actor_user_id=actor_user_id, request_id=request_id,
        )
        self.session.commit()
        return piece

    # --- MVP-24: ContentDistribution <-> TrackingRequirement association --
    # IDENTITY-ONLY HISTORICAL ASSOCIATION (MVP-24A-R1): neither method
    # below reads, copies, or persists TrackingRequirement.status or
    # TrackingPlan.status — only the pair identity and when it was
    # declared. Mutability is governed exclusively by
    # ContentDistribution.status (READY = mutable, DISTRIBUTED = frozen)
    # — never by TrackingPlan.status (CERTIFIED included, MVP-24A-R1 §D/§M).
    #
    # Both methods take an already-resolved ``TrackingRequirement`` —
    # the same "creation methods take already-loaded parent domain
    # objects" shape this module's own docstring documents for
    # cross-context callers (mirrors ``LearningService.record_learning_
    # candidate`` taking an already-resolved ``AnalysisResult``). The
    # router resolves it campaign-scoped via
    # ``TrackingRequirementRepository.get_for_campaign_by_public_id``
    # BEFORE calling either method here — same-Campaign integrity for the
    # TrackingRequirement side is proven there, not re-derived here,
    # since TrackingRequirement carries no workspace_id to re-check
    # against (MVP-24A-R1 §R).
    #
    # Locks the owning ContentPiece (``_load_piece_for_transition``), not
    # a direct FOR UPDATE on ContentDistribution — this is the identical
    # row ``record_distributed`` above already locks, and is the only way
    # this mutation genuinely serializes against it; a lock placed
    # directly on ContentDistribution instead would not contend with
    # ``record_distributed``'s own existing Piece-row lock at all.

    def associate_tracking_requirement(
        self,
        *,
        workspace_id: uuid.UUID,
        content_piece_public_id: str,
        tracking_requirement: TrackingRequirement,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> ContentDistribution:
        piece = self._load_piece_for_transition(workspace_id=workspace_id, content_piece_public_id=content_piece_public_id)
        distribution = self.distributions.get_for_piece(piece.id)
        if distribution is None or distribution.status is not ContentDistributionStatus.READY:
            raise InvalidLifecycleTransitionError()

        try:
            with self.session.begin_nested():
                self.distribution_tracking_requirements.create(
                    distribution=distribution, tracking_requirement=tracking_requirement
                )
        except IntegrityError:
            raise TrackingRequirementAlreadyAssociatedError() from None

        self.events.record(
            workspace_id=piece.workspace_id, event_type=EVENT_TRACKING_REQUIREMENT_ASSOCIATED,
            actor_type=ActorType.USER, distribution_id=distribution.id,
            tracking_requirement_id=tracking_requirement.id,
            actor_user_id=actor_user_id, request_id=request_id,
        )
        self.session.commit()
        return distribution

    def dissociate_tracking_requirement(
        self,
        *,
        workspace_id: uuid.UUID,
        content_piece_public_id: str,
        tracking_requirement: TrackingRequirement,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> ContentDistribution:
        piece = self._load_piece_for_transition(workspace_id=workspace_id, content_piece_public_id=content_piece_public_id)
        distribution = self.distributions.get_for_piece(piece.id)
        if distribution is None or distribution.status is not ContentDistributionStatus.READY:
            raise InvalidLifecycleTransitionError()

        association = self.distribution_tracking_requirements.get(
            content_distribution_id=distribution.id, tracking_requirement_id=tracking_requirement.id
        )
        if association is None:
            raise TrackingRequirementNotAssociatedError()
        self.distribution_tracking_requirements.delete(association)

        self.events.record(
            workspace_id=piece.workspace_id, event_type=EVENT_TRACKING_REQUIREMENT_DISSOCIATED,
            actor_type=ActorType.USER, distribution_id=distribution.id,
            tracking_requirement_id=tracking_requirement.id,
            actor_user_id=actor_user_id, request_id=request_id,
        )
        self.session.commit()
        return distribution

    def list_tracking_requirement_ids_for_distribution(self, content_distribution_id: uuid.UUID) -> list:
        return self.distribution_tracking_requirements.list_tracking_requirement_ids_for_distribution(content_distribution_id)

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
        """Legal only from DRAFT (MVP-20A-R1, CONTENT-P0-6). Although
        ``REVISION_REQUESTED -> IN_PRODUCTION`` remains a legal edge in
        ``app/content/transitions.py`` (``create_revision_version`` needs
        it), this generic command is deliberately narrower than the graph:
        it must never be the route that lets a Piece reach IN_PRODUCTION
        from REVISION_REQUESTED without a new immutable ContentVersion
        having been created first — that would silently recreate the
        same-version-reapproval defect MVP-20 exists to close. State-machine
        legality != command-level authority; only ``create_revision_version``
        is authorized to consume that specific edge."""
        piece = self._load_piece_for_transition(workspace_id=workspace_id, content_piece_public_id=content_piece_public_id)
        if piece.status is not ContentPieceStatus.DRAFT:
            raise InvalidLifecycleTransitionError(
                f"mark_in_production is only legal from DRAFT (piece is {piece.status.value})."
            )
        return self._apply_piece_transition(
            piece=piece, target=ContentPieceStatus.IN_PRODUCTION, actor_user_id=actor_user_id, request_id=request_id
        )

    def create_revision_version(
        self, *, workspace_id: uuid.UUID, content_piece_public_id: str, payload: dict,
        created_by_user_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None, request_id: str | None = None,
    ) -> ContentVersion:
        """MVP-20 / CONTENT-P0-6: the sole production command authorized to
        consume ``REVISION_REQUESTED -> IN_PRODUCTION`` — atomically
        creates the new immutable ContentVersion the revision cycle
        requires together with that transition, in one commit, so no route
        can ever reach IN_PRODUCTION from REVISION_REQUESTED without a
        fresh Version backing it. Mirrors ``record_piece``'s own
        "creation + transition, one transaction, one commit" shape, but as
        a lock-first transition method (MVP-17A-R1 defensive shape) since
        it mutates an existing Piece rather than creating one."""
        piece = self._load_piece_for_transition(workspace_id=workspace_id, content_piece_public_id=content_piece_public_id)
        if piece.status is not ContentPieceStatus.REVISION_REQUESTED:
            raise InvalidLifecycleTransitionError(
                f"A new content version can only be created while the piece is REVISION_REQUESTED (currently {piece.status.value})."
            )
        version = self.versions.create(content_piece=piece, payload=payload, created_by_user_id=created_by_user_id)
        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=piece.workspace_id,
            event_type=EVENT_VERSION_RECORDED,
            actor_type=actor_type,
            content_piece_id=piece.id,
            content_version_id=version.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        previous = piece.status
        piece.status = ContentPieceStatus.IN_PRODUCTION
        self.events.record(
            workspace_id=piece.workspace_id,
            event_type=EVENT_PIECE_STATUS_CHANGED,
            actor_type=actor_type,
            content_piece_id=piece.id,
            content_version_id=version.id,
            previous_state=previous.value,
            new_state=piece.status.value,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return version

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
        self, *, workspace_id: uuid.UUID, content_piece_public_id: str,
        actor_user_id: uuid.UUID | None = None, request_id: str | None = None,
    ) -> ContentApproval:
        """Creates ContentApproval(status=REQUESTED). No governance
        decision is made here — this only opens a request, the same way
        ``OrchestrationService.create_decision_request`` opens a Human
        Decision Request without itself resolving it.

        MVP-20A-R1 (CONTENT-P3-4/CONTENT-P3-5): reclassified into the
        lock-then-verify "transition method" shape (see the module
        docstring) — locks the owning ContentPiece row FIRST, then
        re-checks ``READY_FOR_REVIEW`` and resolves the current
        ContentVersion, both *after* that lock is held, never before.
        This closes two related races: (P3-4) a caller-supplied or
        pre-lock-resolved ContentVersion could go stale if a concurrent
        ``create_revision_version`` call committed a newer Version in the
        gap between resolution and locking; (P3-5) the Piece's own status
        could change underneath an unlocked pre-lock check (e.g. a
        concurrent ``archive_piece``). Preserves the MVP-17A-R1 concurrency
        repair unchanged: two genuinely concurrent callers still serialize
        on this same lock, and ``get_open_for_version`` is still checked
        under it — the first proceeds and commits, the second blocks,
        observes the winner's just-created row, and is rejected with
        ``ContentApprovalAlreadyOpenError``."""
        piece = self._load_piece_for_transition(workspace_id=workspace_id, content_piece_public_id=content_piece_public_id)
        if piece.status is not ContentPieceStatus.READY_FOR_REVIEW:
            raise InvalidLifecycleTransitionError(
                f"A content piece must be READY_FOR_REVIEW before requesting approval (currently {piece.status.value})."
            )
        version = self.versions.get_latest_for_piece(piece.id)
        if version is None:
            raise ForbiddenError()
        if self.approvals.get_open_for_version(version.id) is not None:
            raise ContentApprovalAlreadyOpenError()

        approval = self.approvals.create(content_version=version)
        self.events.record(
            workspace_id=piece.workspace_id,
            event_type=EVENT_APPROVAL_RECORDED,
            actor_type=ActorType.USER if actor_user_id is not None else ActorType.SYSTEM,
            content_piece_id=piece.id,
            content_version_id=version.id,
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

        APPROVED and CHANGES_REQUESTED are both coupled, atomically, with a
        ContentPiece status transition — APPROVED to
        (READY_FOR_REVIEW -> APPROVED), per the architecture doc's own
        explicit transaction-boundary rule, and CHANGES_REQUESTED to
        (READY_FOR_REVIEW -> REVISION_REQUESTED), per MVP-20. REJECTED
        persists the Approval decision only and never touches
        ContentPiece.status — a deliberate, out-of-scope exception
        (MVP-20A §J/MVP-20A-R1 §17), not an oversight."""
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
        elif decision is ContentApprovalStatus.CHANGES_REQUESTED:
            # MVP-20: closes the same-version-reapproval defect — the
            # Piece must leave READY_FOR_REVIEW so no normal lifecycle
            # route can resubmit the same, unrevised ContentVersion (see
            # create_revision_version and the module docstring).
            piece_target = ContentPieceStatus.REVISION_REQUESTED
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
        # REJECTED: no ContentPiece mutation — deliberate, out-of-scope
        # exception (MVP-20A §J/MVP-20A-R1 §17).

        self.session.commit()
        return approval
