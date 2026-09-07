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
