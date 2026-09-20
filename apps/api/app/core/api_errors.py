"""Typed application errors that map onto the standard API error
envelope (see ``app.core.errors``).

Domain/service code raises these directly — it never constructs a
``JSONResponse`` itself and never returns an HTTP status code. The
exception handler registered in ``app.core.errors`` is the single place
that turns one of these into a wire response, so every error path goes
through the exact same envelope shape.
"""

from __future__ import annotations


class ApiError(Exception):
    """Base class for every stable, client-facing application error."""

    def __init__(self, message: str, *, status_code: int, code: str) -> None:
        self.message = message
        self.status_code = status_code
        self.code = code
        super().__init__(message)


class AuthenticationRequiredError(ApiError):
    """No valid session at all: missing cookie, unrecognized token, or a
    revoked session. Deliberately indistinguishable from one another —
    see ``app/auth/dependencies.py`` for why (non-leaky session state)."""

    def __init__(self, message: str = "Authentication is required.") -> None:
        super().__init__(message, status_code=401, code="AUTHENTICATION_REQUIRED")


class SessionExpiredError(ApiError):
    """A session that is recognized in the database but past its
    ``expires_at``. Kept distinct from ``AuthenticationRequiredError``
    because "please log in again, your session timed out" is useful,
    non-sensitive information about the caller's *own* request — unlike
    login's credential-enumeration concerns, which are about *other*
    people's accounts."""

    def __init__(self, message: str = "Your session has expired. Please log in again.") -> None:
        super().__init__(message, status_code=401, code="SESSION_EXPIRED")


class InvalidCredentialsError(ApiError):
    """Deliberately generic and reused for every login failure mode
    (unknown email, wrong password, disabled account) — see
    ``app/auth/service.py`` for the account-enumeration defense this
    supports."""

    def __init__(self, message: str = "Invalid email or password.") -> None:
        super().__init__(message, status_code=401, code="INVALID_CREDENTIALS")


class CsrfInvalidError(ApiError):
    def __init__(self, message: str = "Missing or invalid CSRF token.") -> None:
        super().__init__(message, status_code=403, code="CSRF_INVALID")


class ForbiddenError(ApiError):
    """Role/membership check failed. Also used for tenant-isolation
    denials (see ``app/workspaces/service.py``): a workspace that exists
    but that the caller has no membership in returns exactly the same
    error as a role check failure, and the same error as a workspace
    that does not exist at all — never revealing which case it was."""

    def __init__(self, message: str = "You do not have access to this resource.") -> None:
        super().__init__(message, status_code=403, code="FORBIDDEN")


class EmailAlreadyRegisteredError(ApiError):
    def __init__(self, message: str = "An account with this email already exists.") -> None:
        super().__init__(message, status_code=409, code="EMAIL_ALREADY_REGISTERED")


class InvalidLifecycleTransitionError(ApiError):
    """Reused for both an invalid CampaignRun transition and an invalid
    RunStageExecution transition (BACKEND-06 §8/§11) — the caller
    attempted a state change that is not a legal edge in the
    centralized transition matrix (``app/orchestration/transitions.py``).
    A deterministic 409, never a raw 500 or a silently-accepted write."""

    def __init__(self, message: str = "This lifecycle transition is not allowed from the current state.") -> None:
        super().__init__(message, status_code=409, code="INVALID_LIFECYCLE_TRANSITION")


class OrchestrationNotInitializedError(ApiError):
    """A run's business stages must be materialized (via the dedicated
    ``initialize`` operation) before it can be started (BACKEND-06 §14/
    §15) — deliberately two separate, explicit steps."""

    def __init__(self, message: str = "This run has not been initialized yet.") -> None:
        super().__init__(message, status_code=409, code="ORCHESTRATION_NOT_INITIALIZED")


class DecisionAlreadyResolvedError(ApiError):
    """A Human Decision Request may only ever receive one Human Decision
    Response (BACKEND-06 §17) — a second response attempt, or a response
    to an already-cancelled/expired request, is a deterministic conflict,
    never a silent overwrite."""

    def __init__(self, message: str = "This decision has already been resolved.") -> None:
        super().__init__(message, status_code=409, code="DECISION_ALREADY_RESOLVED")


class ProvenanceMismatchError(ApiError):
    """BACKEND-07 §10: reused for every "this doesn't line up" provenance
    failure when recording a ResearchReport/AudienceProfile — the given
    CampaignRun does not belong to the given Campaign, does not belong to
    the given Workspace, the given RunStageExecution does not belong to
    the given CampaignRun, or the RunStageExecution's own ``stage`` is not
    the one expected (RESEARCH/AUDIENCE). A plain or composite foreign
    key alone cannot prove all of these; the service layer checks each
    explicitly and raises this one deterministic error for any failure,
    never revealing which specific check failed to a caller that has no
    business knowing (mirrors ``ForbiddenError``'s own non-leaky
    precedent, applied to provenance rather than tenancy)."""

    def __init__(self, message: str = "The supplied run/stage provenance is inconsistent.") -> None:
        super().__init__(message, status_code=409, code="PROVENANCE_MISMATCH")


class VersionConflictError(ApiError):
    """BACKEND-07 §11: two concurrent writers raced to create the same
    ``(campaign_id, version)`` for a ResearchReport or AudienceProfile —
    the database's own unique constraint is authoritative; this error is
    the mapped, deterministic surface for the resulting ``IntegrityError``,
    never a raw 500."""

    def __init__(self, message: str = "This version already exists for this campaign.") -> None:
        super().__init__(message, status_code=409, code="VERSION_CONFLICT")


class PlanItemAlreadyBriefedError(ApiError):
    """BACKEND-10 §9 (Phase 1B §G): a Plan Item may have at most one
    Content Brief — a BACKEND-10 governance schema decision
    (``uq_content_briefs_plan_item_id``), not a BACKEND-01 mandate. The
    mapped, deterministic surface for the resulting ``IntegrityError``."""

    def __init__(self, message: str = "This Plan Item already has a Content Brief.") -> None:
        super().__init__(message, status_code=409, code="PLAN_ITEM_ALREADY_BRIEFED")


class CreativeBriefAlreadyExistsError(ApiError):
    """BACKEND-13 Governance Freeze: a Content Piece may have at most one
    Creative Brief (``uq_creative_briefs_content_piece_id``) — immutable,
    non-replaceable, 0..1 cardinality. The mapped, deterministic surface
    for the resulting ``IntegrityError``, the same pattern
    ``PlanItemAlreadyBriefedError`` already establishes for Content Brief."""

    def __init__(self, message: str = "This Content Piece already has a Creative Brief.") -> None:
        super().__init__(message, status_code=409, code="CREATIVE_BRIEF_ALREADY_EXISTS")


class AssetArchivedError(ApiError):
    """BACKEND-13 §13: an archived Asset may not receive a new
    AssetVersion — archiving removes it from active normal use, and no
    frozen contract permits versioning past that point."""

    def __init__(self, message: str = "This Asset is archived and cannot receive a new version.") -> None:
        super().__init__(message, status_code=409, code="ASSET_ARCHIVED")


class LearningCandidateNotValidatedError(ApiError):
    """BACKEND-14 Governance Freeze §N: a Strategic Recommendation
    Candidate may be recorded only from a VALIDATED Learning Candidate —
    the canonical edge is explicitly ``VALIDATED -> (may produce)
    STRATEGIC_RECOMMENDATION_CANDIDATE``. A plain DB FK cannot enforce a
    status-value invariant on its parent, so this is the mapped,
    deterministic surface for that explicit service-layer check."""

    def __init__(self, message: str = "A Strategic Recommendation Candidate requires a VALIDATED Learning Candidate.") -> None:
        super().__init__(message, status_code=409, code="LEARNING_CANDIDATE_NOT_VALIDATED")


class RecommendationAlreadyDecidedError(ApiError):
    """BACKEND-14 Governance Freeze §M/§15: a Strategic Recommendation
    Candidate's decision is one-shot — NULL may move to ACCEPTED or
    REJECTED exactly once, never reversed, never decided twice. The
    mapped, deterministic surface for a second decision attempt, the same
    "second attempt is a deterministic conflict, never a silent
    overwrite" pattern ``DecisionAlreadyResolvedError``/ContentApproval's
    own "immutable once resolved" precedent already establish."""

    def __init__(self, message: str = "This Strategic Recommendation Candidate has already been decided.") -> None:
        super().__init__(message, status_code=409, code="RECOMMENDATION_ALREADY_DECIDED")


class StrategicImplicationMismatchError(ApiError):
    """MVP-26A-R1 §H: a StrategicImplication resolved under the caller's
    own Campaign scope (so already proven non-leaky/accessible) but
    belonging to a *different* LearningCandidate than the Recommendation
    being created must never be silently accepted — revealing that this
    same-tenant relationship does not hold is not a cross-tenant leak, the
    same reasoning ``RecommendationAlreadyDecidedError`` already applies
    to a same-tenant state conflict."""

    def __init__(self, message: str = "This Strategic Implication does not belong to the target Learning Candidate.") -> None:
        super().__init__(message, status_code=409, code="STRATEGIC_IMPLICATION_MISMATCH")


class TrackingPlanAlreadyExistsError(ApiError):
    """BACKEND-15 Governance Freeze TRK-D01/TRK-D33: Campaign 1 -> 0..1
    TrackingPlan — a Campaign that already has a Tracking Plan may not
    have a second one recorded. A genuinely new shape (every other
    "belongs to Campaign" entity so far is either 0..N-versioned or has
    no cardinality ceiling at all), not force-fit into an existing error
    class."""

    def __init__(self, message: str = "This Campaign already has a Tracking Plan.") -> None:
        super().__init__(message, status_code=409, code="TRACKING_PLAN_ALREADY_EXISTS")


class TrackingRequirementMutationForbiddenError(ApiError):
    """BACKEND-15 Governance Freeze-R: record_tracking_requirement/
    update_tracking_requirement_status are forbidden while the parent
    TrackingPlan is in a state that would make the mutation invalidate an
    existing or terminal assertion (CONFIGURED, VERIFICATION_PENDING
    forbid new requirements; CERTIFIED forbids both). A plain DB FK
    cannot enforce a parent-status-value invariant, so this is the
    mapped, deterministic surface for that explicit service-layer check.
    Distinct from InvalidLifecycleTransitionError (reserved for literal
    TrackingPlan state-graph edge violations on the Plan itself, not a
    Requirement mutation) and from ForbiddenError (reserved for
    authorization/resource-scope failures, not business-rule
    conflicts)."""

    def __init__(self, message: str = "This operation is not permitted while the Tracking Plan is in its current state.") -> None:
        super().__init__(message, status_code=409, code="TRACKING_REQUIREMENT_MUTATION_FORBIDDEN")


class IdempotencyKeyConflictError(ApiError):
    """MVP-11C-A-R1: a ``client_request_id`` is scoped to
    ``(workspace_id, client_request_id)`` only — the same shape MetricEntry
    already established — never to the specific campaign the caller
    intended it for. A campaign-scoped route (e.g.
    ``POST /campaigns/{id}/analysis/run``) must never return another
    campaign's resource just because its key collided with one already
    used elsewhere in the same workspace. A genuinely new shape (no
    existing 409 class means "this key was already used for a different
    target"), so this is a narrow, dedicated exception rather than a
    reuse of e.g. ``VersionConflictError``. Deliberately non-leaky: never
    names the other campaign, its public ID, the other run, or any
    internal identifier."""

    def __init__(self, message: str = "client_request_id has already been used for another operation.") -> None:
        super().__init__(message, status_code=409, code="IDEMPOTENCY_KEY_CONFLICT")


class ContentApprovalAlreadyOpenError(ApiError):
    """MVP-17A-R1 concurrency repair: a ContentVersion may have at most
    one OPEN (REQUESTED/UNDER_REVIEW) ContentApproval at a time — a
    second ``request_approval`` call while one is already open, whether
    sequential or genuinely concurrent, is a deterministic conflict,
    never a silent second row. A genuinely new shape (no existing 409
    class means "this specific child row is already open" as opposed to
    "already exists at all" — ``CreativeBriefAlreadyExistsError``'s 0..1
    lifetime cardinality does not fit, since multiple *terminal*
    ContentApproval attempts remain valid and expected over time), so
    this is a narrow, dedicated exception rather than a reuse of a
    semantically different existing class."""

    def __init__(self, message: str = "An approval is already open for this content.") -> None:
        super().__init__(message, status_code=409, code="CONTENT_APPROVAL_ALREADY_OPEN")


class EvidenceCorrectionTargetStaleError(ApiError):
    """MVP-19B §12/§13/§31: a DistributionMetricEvidence correction may
    target only the current leaf of its correction chain (the row with no
    successor yet) — a second correction attempt against an already-
    superseded row, whether sequential or genuinely concurrent, is a
    deterministic conflict, never a silent branching chain. Distinct from
    ``IdempotencyKeyConflictError`` (reserved for a ``client_request_id``
    collision against a different logical request) — this is reserved for
    a structurally stale *target*, regardless of the correction's own key."""

    def __init__(self, message: str = "This Evidence has already been superseded by a later correction.") -> None:
        super().__init__(message, status_code=409, code="EVIDENCE_CORRECTION_TARGET_STALE")


class EvidencePeriodInvalidError(ApiError):
    """MVP-19B §17: ``period_end`` must fall on or after the Distribution's
    own ``distributed_at`` UTC calendar date — a fact only the server
    knows (the Distribution row), so this cannot be expressed as a pure
    Pydantic field validator on the request body alone."""

    def __init__(self, message: str = "period_end must be on or after the date this content was distributed.") -> None:
        super().__init__(message, status_code=422, code="EVIDENCE_PERIOD_INVALID")


class TrackingRequirementAlreadyAssociatedError(ApiError):
    """MVP-24: a (ContentDistribution, TrackingRequirement) pair may exist
    at most once (``uq_content_distribution_tracking_requirements_pair``)
    — a second association attempt for the same pair, whether sequential
    or genuinely concurrent, is a deterministic conflict, never a silent
    duplicate/no-op. The mapped, deterministic surface for the resulting
    ``IntegrityError``, the same "second attempt is a conflict" pattern
    ``TrackingPlanAlreadyExistsError``/``CreativeBriefAlreadyExistsError``
    already establish."""

    def __init__(self, message: str = "This Tracking Requirement is already associated with this Distribution.") -> None:
        super().__init__(message, status_code=409, code="TRACKING_REQUIREMENT_ALREADY_ASSOCIATED")


class TrackingRequirementNotAssociatedError(ApiError):
    """MVP-24: removing an association that does not exist is a
    deterministic business-state conflict, never a silent no-op — mirrors
    ``RecommendationAlreadyDecidedError``'s own "nothing to do, but say so
    explicitly" precedent rather than treating a missing pair as success."""

    def __init__(self, message: str = "This Tracking Requirement is not associated with this Distribution.") -> None:
        super().__init__(message, status_code=409, code="TRACKING_REQUIREMENT_NOT_ASSOCIATED")


class CommercialObjectiveAlreadySupersededError(ApiError):
    """MVP-27A-R1 §F: a CommercialObjective's supersession is one-shot —
    the one invariant that is not structurally impossible by construction
    (self-supersession/cycles/cross-tenant replacement are, since the
    replacement is always a freshly-created row). A second attempt to
    supersede an already-superseded original, whether sequential or
    genuinely concurrent, is a deterministic conflict, never a silent
    second replacement — mirrors ``EvidenceCorrectionTargetStaleError``'s
    own "may only target the current leaf of its chain" precedent."""

    def __init__(self, message: str = "This Commercial Objective has already been superseded.") -> None:
        super().__init__(message, status_code=409, code="COMMERCIAL_OBJECTIVE_ALREADY_SUPERSEDED")


class OfferAlreadySupersededError(ApiError):
    """MVP-27A-R1 §F: the identical one-shot supersession invariant as
    ``CommercialObjectiveAlreadySupersededError``, applied to Offer."""

    def __init__(self, message: str = "This Offer has already been superseded.") -> None:
        super().__init__(message, status_code=409, code="OFFER_ALREADY_SUPERSEDED")


class CommercialOutcomeCorrectionTargetStaleError(ApiError):
    """MVP-36A-R1 §7/§8: a CommercialOutcome correction may target only
    the current leaf of its correction chain (the row with no successor
    yet) — a second correction attempt against an already-superseded row,
    whether sequential or genuinely concurrent, is a deterministic
    conflict, never a silent branching chain. Mirrors
    ``EvidenceCorrectionTargetStaleError``'s own precedent exactly, one
    bounded context over. Distinct from ``IdempotencyKeyConflictError``
    (reserved for a ``client_request_id`` collision against a different
    logical request) — this is reserved for a structurally stale
    *target*, regardless of the correction's own key."""

    def __init__(self, message: str = "This Commercial Outcome has already been superseded by a later correction.") -> None:
        super().__init__(message, status_code=409, code="COMMERCIAL_OUTCOME_CORRECTION_TARGET_STALE")


class StrategicRecommendationNotAcceptedError(ApiError):
    """MVP-28B (frozen MVP-28A/-R1/-R2 contract, Model C): a StrategicDecision
    may be recorded only against a StrategicRecommendationCandidate whose own
    ``decision`` is ACCEPTED — recording one against a still-undecided or a
    REJECTED Recommendation would either anticipate a workflow judgment that
    hasn't happened yet or duplicate what REJECTED already means. A plain DB
    FK cannot enforce a parent status-value invariant, so this is the
    mapped, deterministic surface for that explicit service-layer check —
    the same role ``LearningCandidateNotValidatedError`` already plays one
    context up."""

    def __init__(self, message: str = "A Strategic Decision requires an ACCEPTED Strategic Recommendation Candidate.") -> None:
        super().__init__(message, status_code=409, code="STRATEGIC_RECOMMENDATION_NOT_ACCEPTED")


class StrategicDecisionAlreadyExistsError(ApiError):
    """MVP-28A-R2 §H/§L: at most one *current* StrategicDecision
    (``superseded_at IS NULL``) may exist per StrategicRecommendationCandidate
    at a time — recording a "first" Decision against a Recommendation that
    already has a current one is a deterministic conflict (the caller should
    supersede the existing current Decision instead), never a silent second
    current row. The database's own partial unique index
    (``uq_strategic_decisions_current_recommendation``) is the actual
    backstop; this is the mapped, deterministic surface for the resulting
    ``IntegrityError`` or for the equivalent pre-check under row lock."""

    def __init__(self, message: str = "This Strategic Recommendation Candidate already has a current Strategic Decision.") -> None:
        super().__init__(message, status_code=409, code="STRATEGIC_DECISION_ALREADY_EXISTS")


class StrategicDecisionAlreadySupersededError(ApiError):
    """MVP-28A-R2 §G: a StrategicDecision's supersession is one-shot — the
    one invariant that is not structurally impossible by construction
    (self-supersession/cycles/cross-tenant replacement are, since the
    replacement is always a freshly-created row). A second attempt to
    supersede an already-superseded original, whether sequential or
    genuinely concurrent, is a deterministic conflict, never a silent
    second replacement — mirrors ``CommercialObjectiveAlreadySupersededError``
    exactly."""

    def __init__(self, message: str = "This Strategic Decision has already been superseded.") -> None:
        super().__init__(message, status_code=409, code="STRATEGIC_DECISION_ALREADY_SUPERSEDED")


class StrategicDecisionNotEligibleForApprovalError(ApiError):
    """MVP-29A §F/§X/§T (frozen contract, implemented MVP-29B): a
    StrategicApproval may be recorded only against a StrategicDecision
    whose own ``decision_type`` is ADOPT and whose own ``superseded_at`` is
    still NULL at commit time — DEFER/DECLINE decisions and any
    already-superseded Decision (including one superseded by a genuinely
    concurrent operation) are all deterministically ineligible. A plain DB
    FK cannot enforce a sibling table's own column-value invariant, so
    this is the mapped, deterministic surface for that explicit
    service-layer check — the same role ``StrategicRecommendationNotAcceptedError``
    already plays one context up."""

    def __init__(self, message: str = "This Strategic Decision is not eligible for Strategic Approval.") -> None:
        super().__init__(message, status_code=409, code="STRATEGIC_DECISION_NOT_ELIGIBLE_FOR_APPROVAL")


class StrategicApprovalAlreadyExistsError(ApiError):
    """MVP-29A §J (frozen contract, implemented MVP-29B): StrategicDecision
    1 -> 0..1 StrategicApproval, with no supersession of its own — a second
    Approval attempt against a Decision that already has one, whether
    sequential or genuinely concurrent, is a deterministic conflict, never
    a silent second row. The database's own plain ``UNIQUE`` constraint on
    ``strategic_decision_id`` is the actual backstop; this is the mapped,
    deterministic surface for the resulting ``IntegrityError`` or for the
    equivalent pre-check under row lock — mirrors
    ``StrategicDecisionAlreadyExistsError`` exactly, one level down."""

    def __init__(self, message: str = "This Strategic Decision already has a Strategic Approval.") -> None:
        super().__init__(message, status_code=409, code="STRATEGIC_APPROVAL_ALREADY_EXISTS")


class StrategyRevisionNotEligibleError(ApiError):
    """MVP-30A/-30A-R1 (frozen contract, implemented MVP-30B): a governed
    Strategy Revision may be recorded only against a StrategicApproval whose
    own outcome is APPROVED and whose own StrategicDecision is, at the
    locked eligibility point, ADOPT-typed and current (``superseded_at IS
    NULL``) — REJECTED Approvals, DEFER/DECLINE Decisions, and any
    already-superseded Decision (including one superseded by a genuinely
    concurrent operation that commits first) are all deterministically
    ineligible, never silently accepted. The same role
    ``StrategicDecisionNotEligibleForApprovalError`` already plays one
    context up."""

    def __init__(self, message: str = "This Strategic Approval is not eligible to authorize a Strategy Revision.") -> None:
        super().__init__(message, status_code=409, code="STRATEGY_REVISION_NOT_ELIGIBLE")


class StrategicApprovalAlreadyConsumedError(ApiError):
    """MVP-30A-R1 §H (frozen contract, implemented MVP-30B): StrategicApproval
    1 -> 0..1 StrategyRevision — a second Revision attempt using an Approval
    that has already authorized one, whether sequential or genuinely
    concurrent, is a deterministic conflict, never a silent second Strategy
    version. The database's own plain ``UNIQUE`` constraint on
    ``strategic_approval_id`` (``app/orchestration/models.py::StrategyRevision``)
    is the actual backstop; this is the mapped, deterministic surface for
    the resulting ``IntegrityError`` or for the equivalent pre-check under
    row lock — mirrors ``StrategicApprovalAlreadyExistsError`` exactly, one
    level down."""

    def __init__(self, message: str = "This Strategic Approval has already authorized a Strategy Revision.") -> None:
        super().__init__(message, status_code=409, code="STRATEGIC_APPROVAL_ALREADY_CONSUMED")


class StrategyRevisionBaseStaleError(ApiError):
    """MVP-30A-R1 §J/§K (frozen contract, implemented MVP-30B): a governed
    Strategy Revision may only target the Campaign's own current Strategy
    version (highest ``version``) at the moment its base-Strategy row is
    locked — a Revision attempt naming a base Strategy that is no longer
    current, whether because a prior Revision already committed or because
    the caller's own view is simply stale, is a deterministic conflict,
    never silently redirected to whatever is current now and never silently
    rebased. Mirrors ``StrategicDecisionAlreadySupersededError``'s own "may
    only target the current leaf" precedent exactly."""

    def __init__(self, message: str = "This Strategy version is no longer current and cannot be revised.") -> None:
        super().__init__(message, status_code=409, code="STRATEGY_REVISION_BASE_STALE")


class HypothesisStrategyStaleError(ApiError):
    """MVP-31A §8/MVP-31B: a governed Hypothesis may only be created
    against the Campaign's own current Strategy version (highest
    ``version``) at the moment the targeted Strategy row is locked — a
    request naming a Strategy that is no longer current, whether because a
    concurrent StrategyRevision already committed or because the caller's
    own view is simply stale, is a deterministic conflict, never silently
    redirected to whatever is current now and never silently rebased.
    Mirrors ``StrategyRevisionBaseStaleError``'s own precedent exactly, one
    level down."""

    def __init__(self, message: str = "This Strategy version is no longer current and cannot receive a new Hypothesis.") -> None:
        super().__init__(message, status_code=409, code="HYPOTHESIS_STRATEGY_STALE")


class ExperimentStrategyStaleError(ApiError):
    """MVP-32A-R1 §9/§14/MVP-32B: a governed Experiment may only be
    created under a Hypothesis whose own parent Strategy is still the
    Campaign's current version at the moment that Strategy row is locked
    — a request naming a Hypothesis whose Strategy is no longer current,
    whether because a concurrent StrategyRevision already committed or
    because the caller's own view is simply stale, is a deterministic
    conflict, never silently redirected to whatever is current now and
    never silently rebased. Mirrors ``HypothesisStrategyStaleError``'s own
    precedent exactly, one level down."""

    def __init__(
        self, message: str = "This Hypothesis belongs to a Strategy that is no longer current and cannot receive a new Experiment."
    ) -> None:
        super().__init__(message, status_code=409, code="EXPERIMENT_STRATEGY_STALE")


class ExperimentDefinitionStrategyStaleError(ApiError):
    """MVP-37B §Q: a NEW Experiment Definition version may only be written
    for an Experiment whose own parent Strategy is still the Campaign's
    current version at the moment that Strategy row is locked — the same
    Option-A rule as ``ExperimentStrategyStaleError``, one level down.
    History reads and matching replays of an already-committed write are
    unaffected."""

    def __init__(
        self,
        message: str = "This Experiment belongs to a Strategy that is no longer current and cannot receive a new definition version.",
    ) -> None:
        super().__init__(message, status_code=409, code="EXPERIMENT_DEFINITION_STRATEGY_STALE")


class ExperimentDefinitionBaseStaleError(ApiError):
    """MVP-37B §N/§O: ``base_version`` did not equal the Experiment's current
    definition tip at the moment the Experiment row was locked (or two
    concurrent writers raced for the same ``(experiment_id, version)`` and
    the database's own unique constraint decided). Mirrors
    ``StrategyRevisionBaseStaleError``."""

    def __init__(self, message: str = "The Experiment definition changed; refresh and retry from the current version.") -> None:
        super().__init__(message, status_code=409, code="EXPERIMENT_DEFINITION_BASE_STALE")


class ExperimentDefinitionUnchangedError(ApiError):
    """MVP-37B §O: a revision whose normalized content equals the current
    tip is rejected — it would add a version carrying no new declaration."""

    def __init__(self, message: str = "This revision is identical to the current Experiment definition.") -> None:
        super().__init__(message, status_code=409, code="EXPERIMENT_DEFINITION_UNCHANGED")


class ExperimentDefinitionPinnedError(ApiError):
    """MVP-38B §E: a NEW Experiment Definition version cannot be written
    while a governed pinning child (today: a Variant) pins the current tip.
    Deliberately generic — never names the pinning child."""

    def __init__(
        self, message: str = "This Experiment definition is pinned by declared conditions and cannot receive a new version."
    ) -> None:
        super().__init__(message, status_code=409, code="EXPERIMENT_DEFINITION_PINNED")


class ExperimentVariantStrategyStaleError(ApiError):
    """MVP-38B §P: a new Variant may only be declared for an Experiment
    whose parent Strategy is still the Campaign's current version (the
    same Option-A rule as ``ExperimentDefinitionStrategyStaleError``)."""

    def __init__(
        self,
        message: str = "This Experiment belongs to a Strategy that is no longer current and cannot receive a new condition.",
    ) -> None:
        super().__init__(message, status_code=409, code="EXPERIMENT_VARIANT_STRATEGY_STALE")


class ExperimentVariantDefinitionVersionNotCurrentError(ApiError):
    """MVP-38B §D: the requested ``definition_version_id`` resolves inside
    the Experiment but is not the current tip at the moment the Experiment
    row is locked — never silently rebased onto the current tip."""

    def __init__(
        self, message: str = "The requested definition version is not the current version of this Experiment."
    ) -> None:
        super().__init__(message, status_code=409, code="EXPERIMENT_VARIANT_DEFINITION_VERSION_NOT_CURRENT")


class ExperimentVariantLabelDuplicateError(ApiError):
    """MVP-38B §J: a Variant with the same (normalized) label is already
    declared under the pinned definition version."""

    def __init__(self, message: str = "A condition with this label already exists for this definition version.") -> None:
        super().__init__(message, status_code=409, code="EXPERIMENT_VARIANT_LABEL_DUPLICATE")


class MeasurementContractStrategyStaleError(ApiError):
    """MVP-39B §Z: a new Measurement Contract version may only be written
    for an Experiment whose parent Strategy is still the Campaign's current
    version — the same Option-A rule as
    ``ExperimentDefinitionStrategyStaleError``/``ExperimentVariantStrategyStaleError``."""

    def __init__(
        self,
        message: str = "This Experiment belongs to a Strategy that is no longer current and cannot receive a new measurement contract.",
    ) -> None:
        super().__init__(message, status_code=409, code="MEASUREMENT_CONTRACT_STRATEGY_STALE")


class MeasurementContractBaseStaleError(ApiError):
    """MVP-39B §X: ``base_version`` did not equal the Experiment's current
    Measurement Contract tip at the moment the Experiment row was locked (or
    two concurrent writers raced for the same ``(experiment_id, version)``).
    Mirrors ``ExperimentDefinitionBaseStaleError``."""

    def __init__(
        self, message: str = "The measurement contract changed; refresh and retry from the current version."
    ) -> None:
        super().__init__(message, status_code=409, code="MEASUREMENT_CONTRACT_BASE_STALE")


class MeasurementContractUnchangedError(ApiError):
    """MVP-39B §X: a revision whose normalized content equals the current
    tip is rejected — it would add a version carrying no new declaration."""

    def __init__(self, message: str = "This revision is identical to the current measurement contract.") -> None:
        super().__init__(message, status_code=409, code="MEASUREMENT_CONTRACT_UNCHANGED")


class MeasurementContractDefinitionVersionNotCurrentError(ApiError):
    """MVP-39B §Y: the requested ``definition_version_id`` resolves inside
    the Experiment but is not the current tip at the moment the Experiment
    row is locked — never silently rebased onto the current tip. Mirrors
    ``ExperimentVariantDefinitionVersionNotCurrentError``."""

    def __init__(
        self, message: str = "The requested definition version is not the current version of this Experiment."
    ) -> None:
        super().__init__(message, status_code=409, code="MEASUREMENT_CONTRACT_DEFINITION_VERSION_NOT_CURRENT")


class MeasurementContractSuccessCriterionRequiredError(ApiError):
    """MVP-39B §U/§10: a CONTROLLED comparison requires ``success_criterion``
    — a fact only the server knows (the pinned Definition's own
    ``comparison_type``), so this cannot be expressed as a pure Pydantic
    field validator on the request body alone (mirrors
    ``EvidencePeriodInvalidError``'s own precedent exactly)."""

    def __init__(self, message: str = "A CONTROLLED comparison requires a success_criterion.") -> None:
        super().__init__(message, status_code=422, code="MEASUREMENT_CONTRACT_SUCCESS_CRITERION_REQUIRED")
