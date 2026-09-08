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
