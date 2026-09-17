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
from app.core.api_errors import ForbiddenError, InvalidLifecycleTransitionError, LearningCandidateNotValidatedError, RecommendationAlreadyDecidedError, StrategicImplicationMismatchError
from app.learning.models import LearningCandidate, LearningCandidateStatus, StrategicImplication, StrategicRecommendationCandidate, StrategicRecommendationDecision
from app.learning.repository import LearningCandidateRepository, LearningDerivationRepository, StrategicImplicationRepository, StrategicRecommendationCandidateRepository
from app.learning.transitions import is_legal_learning_candidate_transition
from app.measurement.models import AnalysisResult
from app.measurement.repository import AnalysisResultRepository

EVENT_CANDIDATE_RECORDED = "learning.candidate.recorded"
EVENT_CANDIDATE_STATUS_CHANGED = "learning.candidate.status_changed"
EVENT_RECOMMENDATION_RECORDED = "learning.recommendation.recorded"
EVENT_RECOMMENDATION_DECIDED = "learning.recommendation.decided"
EVENT_STRATEGIC_IMPLICATION_RECORDED = "learning.strategic_implication.recorded"


class LearningService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.candidates = LearningCandidateRepository(session)
        self.recommendations = StrategicRecommendationCandidateRepository(session)
        self.implications = StrategicImplicationRepository(session)
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
        graph (Governance Freeze §J/§10). Public callers authorize roles
        before this command. MVP-25 validates fresh qualification under lock."""
        # Re-read even for service callers that already loaded this instance.
        # populate_existing prevents the identity map from retaining pre-lock state.
        learning_candidate = self.candidates.get_by_id(learning_candidate.id, for_update=True)
        if not is_legal_learning_candidate_transition(learning_candidate.status, target_status):
            raise InvalidLifecycleTransitionError(
                f"Cannot transition a learning candidate from {learning_candidate.status.value} to {target_status.value}."
            )
        if target_status == LearningCandidateStatus.VALIDATED:
            from app.learning.qualification import QualificationService
            QualificationService(self.session).require_sufficient(learning_candidate)
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

    # --- StrategicImplication: service-layer creation (MVP-26) ----------

    def record_strategic_implication(
        self,
        *,
        learning_candidate: LearningCandidate,
        statement: str,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> StrategicImplication:
        """Canonical lock (MVP-26 §12): re-reads the candidate FOR UPDATE
        even for callers that already loaded it, exactly like
        ``transition_learning_candidate`` above — status and qualification
        sufficiency are both evaluated fresh, under lock, never against
        stale pre-lock state. Requires VALIDATED + a qualification that
        still satisfies ``QualificationService.require_sufficient``
        (MVP-26 §11) — this second check is intentionally defense-in-depth:
        every currently reachable write path already guarantees it (MVP-25's
        own unconditional gate on the VALIDATED transition), but an
        anomalous historical/imported row must still fail safely rather
        than silently produce an Implication (MVP-26A-R1 §21/§W). Never
        repairs, backfills, or invents qualification/evidence for such a
        row. Cardinality is deliberately 0..N (MVP-26 §7) — no uniqueness
        constraint, two calls create two rows."""
        learning_candidate = self.candidates.get_by_id(learning_candidate.id, for_update=True)
        if learning_candidate.status is not LearningCandidateStatus.VALIDATED:
            raise LearningCandidateNotValidatedError()
        from app.learning.qualification import QualificationService

        QualificationService(self.session).require_sufficient(learning_candidate)

        implication = self.implications.create(learning_candidate=learning_candidate, statement=statement)

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=learning_candidate.workspace_id,
            event_type=EVENT_STRATEGIC_IMPLICATION_RECORDED,
            actor_type=actor_type,
            learning_candidate_id=learning_candidate.id,
            strategic_implication_id=implication.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return implication

    def list_implications_for_campaign(self, *, campaign_id: uuid.UUID) -> list[StrategicImplication]:
        return self.implications.list_for_campaign(campaign_id)

    # --- StrategicRecommendationCandidate: service-layer creation -------

    def record_strategic_recommendation_candidate(
        self,
        *,
        campaign: Campaign,
        learning_candidate: LearningCandidate,
        strategic_implication_public_id: str,
        summary: str,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> StrategicRecommendationCandidate:
        """Requires an already-VALIDATED parent (Governance Freeze §N) —
        a plain FK cannot enforce this status-value invariant, so it is
        checked explicitly, and checked FIRST (MVP-26A-R1 §F/§22 defense-
        in-depth), before the implication is even resolved — this
        preserves the pre-existing ``LEARNING_CANDIDATE_NOT_VALIDATED``
        contract for a too-early call exactly as MVP-23A established it,
        rather than masking it behind an implication-not-found response.

        MVP-26/26A-R1: ``strategic_implication_public_id`` is REQUIRED, no
        default, and resolved here under the *same* Campaign scope as
        ``learning_candidate`` — mirroring
        ``decide_strategic_recommendation_candidate``'s own campaign-scoped
        resolution exactly, never trusting a client-supplied internal
        UUID. Nonexistent/wrong-workspace/wrong-campaign-same-workspace are
        indistinguishable (``ForbiddenError``). A same-campaign Implication
        belonging to a *different* LearningCandidate is a distinct,
        same-tenant relational conflict (``StrategicImplicationMismatchError``,
        MVP-26A-R1 §7/§H) — the caller already proved campaign-scoped
        access to a real Implication, so revealing this specific mismatch
        is not a cross-tenant leak. This is the sole production call site
        of this method (``app/learning/router.py``); legacy rows created
        before this requirement existed keep ``strategic_implication_id =
        NULL`` untouched — no backfill, no synthetic Implication. Never
        mutates the parent LearningCandidate; never creates a
        CampaignVersion; never touches Strategy."""
        if learning_candidate.status is not LearningCandidateStatus.VALIDATED:
            raise LearningCandidateNotValidatedError()
        strategic_implication = self.implications.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=strategic_implication_public_id
        )
        if strategic_implication is None:
            raise ForbiddenError()
        if strategic_implication.learning_candidate_id != learning_candidate.id:
            raise StrategicImplicationMismatchError()

        recommendation = self.recommendations.create(
            learning_candidate=learning_candidate, summary=summary, strategic_implication=strategic_implication
        )

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=learning_candidate.workspace_id,
            event_type=EVENT_RECOMMENDATION_RECORDED,
            actor_type=actor_type,
            learning_candidate_id=learning_candidate.id,
            strategic_recommendation_candidate_id=recommendation.id,
            strategic_implication_id=strategic_implication.id,
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
