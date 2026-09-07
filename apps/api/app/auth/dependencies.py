"""Reusable FastAPI dependencies for authentication, workspace context,
and role authorization.

These are the only places a route should ever obtain "who is calling"
or "which workspace" — never by reading a `workspace_id` out of request
JSON/query params and trusting it (BACKEND-04 §19/§22). Chaining them
(`get_current_workspace` depends on `get_current_membership` depends on
`get_current_user` depends on `get_current_session`) means a route that
declares `Depends(get_current_workspace)` gets the *entire* chain
verified — there is no way to reach a workspace without first proving a
valid session, a real user, and an active membership.

AUTHENTICATED != AUTHORIZED FOR WORKSPACE: `get_current_user` only
proves *who* is calling; `get_current_membership`/`get_current_workspace`
are the separate step that proves *what workspace* they may act in, and
BOTH must resolve for a workspace-scoped operation to run at all.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth.csrf import CSRF_HEADER_NAME
from app.auth.models import AuthSession
from app.auth.repository import AuthSessionRepository
from app.auth.security import constant_time_equals, hash_token
from app.core.api_errors import AuthenticationRequiredError, CsrfInvalidError, ForbiddenError, SessionExpiredError
from app.core.config import Settings, get_settings
from app.persistence.session import get_db
from app.users.models import User
from app.users.repository import UserRepository
from app.workspaces.models import Membership, MembershipRole, Workspace
from app.workspaces.repository import MembershipRepository, WorkspaceRepository


def get_current_session(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthSession:
    raw_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not raw_token:
        raise AuthenticationRequiredError()

    auth_session = AuthSessionRepository(db).get_by_token_hash(hash_token(raw_token))
    if auth_session is None or auth_session.revoked_at is not None:
        # A garbage/unknown token and a revoked session are
        # intentionally indistinguishable from "no session at all" —
        # see app/core/api_errors.py::AuthenticationRequiredError.
        raise AuthenticationRequiredError()

    if auth_session.expires_at <= datetime.now(timezone.utc):
        raise SessionExpiredError()

    return auth_session


def get_current_user(
    auth_session: AuthSession = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> User:
    user = UserRepository(db).get_by_id(auth_session.user_id)
    if user is None:  # pragma: no cover - would mean an orphaned session row
        raise AuthenticationRequiredError()
    return user


def get_current_membership(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Membership:
    membership = MembershipRepository(db).get_first_active_for_user(user.id)
    if membership is None:
        raise ForbiddenError("Your account has no active workspace membership.")
    return membership


def get_current_workspace(
    membership: Membership = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> Workspace:
    workspace = WorkspaceRepository(db).get_by_id(membership.workspace_id)
    if workspace is None:  # pragma: no cover - would mean an orphaned membership row
        raise ForbiddenError()
    return workspace


def require_csrf(
    request: Request,
    auth_session: AuthSession = Depends(get_current_session),
) -> None:
    """Dependency for every authenticated state-changing route (POST/
    PUT/PATCH/DELETE). GET/HEAD/OPTIONS must never depend on this."""
    provided = request.headers.get(CSRF_HEADER_NAME)
    if not provided or not constant_time_equals(provided, auth_session.csrf_secret):
        raise CsrfInvalidError()


def require_role(*roles: MembershipRole) -> Iterable:
    """Returns a dependency that additionally requires the caller's
    current-workspace role to be one of ``roles``. Not currently wired
    to any route in BACKEND-04 (there is no role-gated endpoint yet),
    but implemented and tested as the reusable primitive future
    workspace-admin endpoints will depend on.

    ROLE != GOVERNANCE AUTHORITY: this only ever answers "is this
    membership's role one of X" — it has no relationship to, and grants
    no authority over, AI governance decisions (Content Approval, Gate
    Decisions, etc. — see docs/backend/BACKEND-01-ARCHITECTURE.md §4). A
    workspace OWNER is not AGENT-00 and this dependency does not pretend
    otherwise.
    """

    def _dependency(membership: Membership = Depends(get_current_membership)) -> Membership:
        if membership.role not in roles:
            raise ForbiddenError()
        return membership

    return _dependency
