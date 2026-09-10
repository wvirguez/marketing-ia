"""Learning persistence — BACKEND-14.

No public HTTP write endpoint exists for LearningCandidate anywhere in this
module — the frozen API surface (Governance Freeze §K/§S) is GET plus one
narrow PATCH scoped to a StrategicRecommendationCandidate's decision only.
``record_learning_candidate``/``transition_learning_candidate``/
``record_strategic_recommendation_candidate`` are service-layer-only,
exactly like every derived/computed entity in every prior stage — callers
today are tests, and in the future a separately-authorized orchestration/
agent runtime.

CAMPAIGN-SCOPED RESOURCE INTEGRITY (Governance Freeze-R, GF-D26):
``decide_strategic_recommendation_candidate`` takes an already-loaded,
already-authorized ``Campaign`` — never a bare ``workspace_id`` — because
active Workspace membership alone never proves a given recommendation
belongs to the Campaign named in the URL. The lookup is campaign-scoped via
``StrategicRecommendationCandidateRepository.get_for_campaign_by_public_id``,
which joins through ``LearningCandidate -> AnalysisResult.campaign_id``.

PERSISTING A LEARNING CANDIDATE != VALIDATED LEARNING. AN ACCEPTED
STRATEGIC RECOMMENDATION CANDIDATE != A STRATEGIC DECISION != AN AUTOMATIC
STRATEGY MUTATION != AN AUTOMATIC CAMPAIGNVERSION. Nothing here mutates
``AnalysisResult``, ``Strategy``, ``Campaign``, ``Content``, or ``Assets``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign
from app.core.api_errors import ForbiddenError, InvalidLifecycleTransitionError, LearningCandidateNotValidatedError, RecommendationAlreadyDecidedError
from app.learning.models import LearningCandidate, LearningCandidateStatus, StrategicRecommendationCandidate, StrategicRecommendationDecision
from app.learning.repository import LearningCandidateRepository, StrategicRecommendationCandidateRepository
from app.learning.transitions import is_legal_learning_candidate_transition
from app.measurement.models import AnalysisResult

EVENT_CANDIDATE_RECORDED = "learning.candidate.recorded"
EVENT_CANDIDATE_STATUS_CHANGED = "learning.candidate.status_changed"
EVENT_RECOMMENDATION_RECORDED = "learning.recommendation.recorded"
EVENT_RECOMMENDATION_DECIDED = "learning.recommendation.decided"


class LearningService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.candidates = LearningCandidateRepository(session)
        self.recommendations = StrategicRecommendationCandidateRepository(session)
        self.events = AuditEventRepository(session)

    # --- reads (GET-only public surface calls these) -----------------

    def list_candidates_for_campaign(self, *, campaign_id: uuid.UUID) -> list[LearningCandidate]:
        return self.candidates.list_for_campaign(campaign_id)

    def list_recommendations_for_campaign(self, *, campaign_id: uuid.UUID) -> list[StrategicRecommendationCandidate]:
        return self.recommendations.list_for_campaign(campaign_id)

    # --- LearningCandidate: service-layer only -------------------------

    def record_learning_candidate(
        self,
        *,
        analysis_result: AnalysisResult,
        summary: str,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> LearningCandidate:
        """Always created at CANDIDATE_IDENTIFIED (Governance Freeze §J) —
        the caller cannot supply an arbitrary initial status."""
        candidate = self.candidates.create(analysis_result=analysis_result, summary=summary)

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=analysis_result.workspace_id,
            event_type=EVENT_CANDIDATE_RECORDED,
            actor_type=actor_type,
            learning_candidate_id=candidate.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return candidate

    def transition_learning_candidate(
        self,
        *,
        learning_candidate: LearningCandidate,
        target_status: LearningCandidateStatus,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> LearningCandidate:
        """One generic transition command, validated against the frozen
        graph (Governance Freeze §J/§10) — no separate validate/reject/
        provisional methods. Service-layer only; no public route."""
        if not is_legal_learning_candidate_transition(learning_candidate.status, target_status):
            raise InvalidLifecycleTransitionError(
                f"Cannot transition a learning candidate from {learning_candidate.status.value} to {target_status.value}."
            )
        previous = learning_candidate.status
        learning_candidate.status = target_status

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=learning_candidate.workspace_id,
            event_type=EVENT_CANDIDATE_STATUS_CHANGED,
            actor_type=actor_type,
            learning_candidate_id=learning_candidate.id,
            previous_state=previous.value,
            new_state=target_status.value,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return learning_candidate

    # --- StrategicRecommendationCandidate: service-layer creation -------

    def record_strategic_recommendation_candidate(
        self,
        *,
        learning_candidate: LearningCandidate,
        summary: str,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> StrategicRecommendationCandidate:
        """Requires an already-VALIDATED parent (Governance Freeze §N) —
        a plain FK cannot enforce this status-value invariant, so it is
        checked explicitly. Never mutates the parent LearningCandidate;
        never creates a CampaignVersion; never touches Strategy."""
        if learning_candidate.status is not LearningCandidateStatus.VALIDATED:
            raise LearningCandidateNotValidatedError()

        recommendation = self.recommendations.create(learning_candidate=learning_candidate, summary=summary)

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=learning_candidate.workspace_id,
            event_type=EVENT_RECOMMENDATION_RECORDED,
            actor_type=actor_type,
            learning_candidate_id=learning_candidate.id,
            strategic_recommendation_candidate_id=recommendation.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return recommendation

    # --- StrategicRecommendationCandidate: one-shot public decision -----

    def decide_strategic_recommendation_candidate(
        self,
        *,
        campaign: Campaign,
        recommendation_public_id: str,
        decision: StrategicRecommendationDecision,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> StrategicRecommendationCandidate:
        """Campaign-scoped resource integrity (Governance Freeze-R
        GF-D26): ``campaign`` must already be resolved and authorized by
        the caller (workspace membership proven) — this method additionally
        proves the target recommendation belongs, transitively, to this
        exact Campaign, never merely to the same Workspace. Nonexistent,
        wrong-workspace, and wrong-campaign-same-workspace are all
        indistinguishable, the same non-leaky convention every other
        campaign-scoped lookup in this codebase already uses."""
        recommendation = self.recommendations.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=recommendation_public_id, for_update=True
        )
        if recommendation is None:
            raise ForbiddenError()
        if recommendation.decision is not None:
            raise RecommendationAlreadyDecidedError()

        recommendation.decision = decision
        recommendation.decided_at = datetime.now(timezone.utc)

        self.events.record(
            workspace_id=recommendation.workspace_id,
            event_type=EVENT_RECOMMENDATION_DECIDED,
            actor_type=ActorType.USER,
            strategic_recommendation_candidate_id=recommendation.id,
            new_state=decision.value,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return recommendation
