"""Learning persistence — BACKEND-14, extended by MVP-12B.

No public HTTP write endpoint exists for LearningCandidate creation via
arbitrary caller-supplied content — the API surface is GET, one narrow
PATCH scoped to a StrategicRecommendationCandidate's decision, and (as of
MVP-12B) one explicit POST that derives LearningCandidates deterministically
from already-persisted AnalysisResult rows (the "Measurement -> Learning
bridge"). ``record_learning_candidate``/``transition_learning_candidate``/
``record_strategic_recommendation_candidate`` remain service-layer-only,
exactly like every derived/computed entity in every prior stage — direct
callers today are tests and, in the future, a separately-authorized
orchestration/agent runtime.

CAMPAIGN-SCOPED RESOURCE INTEGRITY (Governance Freeze-R, GF-D26):
``decide_strategic_recommendation_candidate`` takes an already-loaded,
already-authorized ``Campaign`` — never a bare ``workspace_id`` — because
active Workspace membership alone never proves a given recommendation
belongs to the Campaign named in the URL. The lookup is campaign-scoped via
``StrategicRecommendationCandidateRepository.get_for_campaign_by_public_id``,
which joins through ``LearningCandidate -> AnalysisResult.campaign_id``.
``derive_candidates_for_campaign`` follows the identical discipline: it only
ever touches AnalysisResult rows already proven to belong to the caller's
authorized Campaign.

THE BRIDGE (MVP-12B-A/-R1/-R2): ``derive_candidate_from_analysis_result``
is the sole caller in this module that creates a LearningCandidate from a
production-reachable, explicitly user-triggered path rather than from a
test. It reuses ``LearningCandidateRepository.create`` directly (not the
committing ``record_learning_candidate``) so the candidate and its
``LearningDerivation`` provenance row can be created atomically as one
unit — see that method's own docstring for the exact concurrency contract.

PERSISTING A LEARNING CANDIDATE != VALIDATED LEARNING. AN ACCEPTED
STRATEGIC RECOMMENDATION CANDIDATE != A STRATEGIC DECISION != AN AUTOMATIC
STRATEGY MUTATION != AN AUTOMATIC CAMPAIGNVERSION. Nothing here mutates
``AnalysisResult``, ``Strategy``, ``Campaign``, ``Content``, or ``Assets``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign
from app.core.api_errors import ForbiddenError, InvalidLifecycleTransitionError, LearningCandidateNotValidatedError, RecommendationAlreadyDecidedError
from app.learning.models import LearningCandidate, LearningCandidateStatus, StrategicRecommendationCandidate, StrategicRecommendationDecision
from app.learning.repository import LearningCandidateRepository, LearningDerivationRepository, StrategicRecommendationCandidateRepository
from app.learning.transitions import is_legal_learning_candidate_transition
from app.measurement.models import AnalysisResult
from app.measurement.repository import AnalysisResultRepository

EVENT_CANDIDATE_RECORDED = "learning.candidate.recorded"
EVENT_CANDIDATE_STATUS_CHANGED = "learning.candidate.status_changed"
EVENT_RECOMMENDATION_RECORDED = "learning.recommendation.recorded"
EVENT_RECOMMENDATION_DECIDED = "learning.recommendation.decided"


class LearningService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.candidates = LearningCandidateRepository(session)
        self.recommendations = StrategicRecommendationCandidateRepository(session)
        self.derivations = LearningDerivationRepository(session)
        self.analysis_results = AnalysisResultRepository(session)
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

    # --- Measurement -> Learning bridge (MVP-12B-A/-R1/-R2) --------------

    def derive_candidate_from_analysis_result(
        self,
        *,
        analysis_result: AnalysisResult,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> tuple[LearningCandidate, bool]:
        """Deterministically derives (or safely reuses) exactly one
        bridge-created LearningCandidate for ``analysis_result``. Returns
        ``(candidate, created)`` — ``created`` is ``True`` only when this
        call is the one that actually persisted a new candidate.

        BRIDGE IDENTITY (MVP-12B-A-R1): a candidate is "bridge-created" iff
        a ``LearningDerivation`` row references it — never inferred from
        content. This check never inspects ``LearningCandidate`` rows
        directly, so a pre-existing, non-bridge candidate for the same
        AnalysisResult (created by any other path) is invisible to it and
        never suppresses this method's own derivation.

        CONCURRENCY (race-safe, DB-backed, exactly like
        ``MeasurementAnalysisService.record_metric_entry``'s own
        ``UNIQUE(workspace_id, client_request_id)`` recovery): this method
        does not pre-check for an existing derivation before attempting to
        write — it attempts the candidate + derivation insert first and
        lets ``learning_derivations``'s own ``UNIQUE(analysis_result_id)``
        constraint be authoritative. Two concurrent callers for the same
        AnalysisResult can both reach this point; exactly one wins the
        derivation insert, and the other observes the ``IntegrityError``,
        rolls back (discarding its own just-flushed, not-yet-committed
        candidate row along with the failed derivation insert — neither
        was ever durable), and returns the winner's already-committed
        candidate instead. This is a genuine idempotent replay, not a
        conflict to surface to the caller — both HTTP requests resolve
        successfully with the identical candidate.

        An IntegrityError whose cause is NOT this exact race (verified by
        re-querying for the winning LearningDerivation row) is never
        silently treated as a successful replay — it is re-raised
        unchanged, exactly like every other insert-then-catch idempotency
        mechanism in this codebase.

        Audit: exactly one ``learning.candidate.recorded`` event is
        emitted, only on the winning path, only after the candidate and
        its derivation row are both durably flushed together — never on a
        losing/replay path, and never before both writes have succeeded
        (so an audit-recording failure leaves neither row committed; the
        request's own session-per-request rollback-on-exception guarantee,
        `app/persistence/session.py`, applies here exactly as it already
        does for `record_learning_candidate`)."""
        try:
            candidate = self.candidates.create(analysis_result=analysis_result, summary=analysis_result.summary)
            self.derivations.create(analysis_result=analysis_result, learning_candidate=candidate)
        except IntegrityError:
            self.session.rollback()
            existing_derivation = self.derivations.get_by_analysis_result_id(analysis_result.id)
            if existing_derivation is None:
                # Not the expected analysis_result_id race — an unrelated,
                # genuine integrity failure. Never invent a false success.
                raise
            existing_candidate = self.candidates.get_by_id(existing_derivation.learning_candidate_id)
            return existing_candidate, False

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
        return candidate, True

    def derive_candidates_for_campaign(
        self,
        *,
        campaign: Campaign,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> None:
        """Processes every AnalysisResult already persisted for
        ``campaign`` — every one qualifies (Governance Freeze: no
        threshold, no metric interpretation, no causal reasoning, no LLM).
        Each AnalysisResult is handled by its own independent call to
        ``derive_candidate_from_analysis_result``, preserving an
        all-or-nothing-per-AnalysisResult transaction boundary: if one
        genuinely fails (a real system error, not the expected
        race-recovery path), any AnalysisResult already processed earlier
        in this same call remains durably committed, and the caller may
        simply invoke this method again — already-bridged AnalysisResults
        are safely skipped (idempotent replay) and only the missing ones
        are retried. No derivation-run entity is introduced."""
        for analysis_result in self.analysis_results.list_for_campaign(campaign.id):
            self.derive_candidate_from_analysis_result(
                analysis_result=analysis_result, actor_user_id=actor_user_id, request_id=request_id
            )
