"""Registration, login, and logout — the only place these operations are
orchestrated. Routes call this service; they never touch a repository
or the password/token primitives directly.

Transaction ownership: every public method here calls
``self.session.commit()`` itself, exactly once, after every write for
that operation has succeeded — this is the "service/application layer
owns explicit write transactions" half of the BACKEND-03 session
contract. If anything raises before that point (including a database
constraint violation), nothing has been committed, and the exception
propagates up to ``get_db``'s own rollback-on-exception handling — so a
partially-completed registration can never be left half-written.

Login enumeration defense (BACKEND-04 §16): ``InvalidCredentialsError``
is raised — with the identical message — whether the email does not
exist, the password is wrong, or the account is disabled. An unknown
email additionally runs a real Argon2id verification against a fixed
dummy hash (``verify_against_dummy_hash``) so a timing side-channel
cannot distinguish "no such user" from "wrong password" either.

Pre-auth CSRF policy (BACKEND-04 §13): register and login are exempt
from the session-bound CSRF check that guards every other authenticated
mutation, because no session/CSRF secret exists yet before they
succeed. The residual "login CSRF" risk (an attacker forcing a victim's
browser to log in as the *attacker's* account) is mitigated by
`SameSite=Lax` on the session cookie and by the fact that a successful
login does not execute any action on behalf of an already-authenticated
identity — it establishes one. This is a deliberate, documented
trade-off, not an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import AuthSession
from app.auth.repository import AuthSessionRepository
from app.auth.security import (
    generate_random_token,
    hash_password,
    hash_token,
    verify_against_dummy_hash,
    verify_password,
)
from app.core.api_errors import EmailAlreadyRegisteredError, InvalidCredentialsError
from app.core.config import Settings, get_settings
from app.users.models import User, UserStatus, normalize_email
from app.users.repository import UserRepository
from app.workspaces.models import Membership, MembershipRole, Workspace
from app.workspaces.repository import MembershipRepository, OrganizationRepository, WorkspaceRepository

_USER_AGENT_SUMMARY_MAX_LENGTH = 120


@dataclass(frozen=True)
class AuthenticatedContext:
    user: User
    workspace: Workspace
    membership: Membership
    session_token: str


def _summarize_user_agent(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    return user_agent[:_USER_AGENT_SUMMARY_MAX_LENGTH]


class AuthService:
    def __init__(self, session: Session, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.users = UserRepository(session)
        self.organizations = OrganizationRepository(session)
        self.workspaces = WorkspaceRepository(session)
        self.memberships = MembershipRepository(session)
        self.auth_sessions = AuthSessionRepository(session)

    def register(
        self,
        *,
        email: str,
        password: str,
        display_name: str,
        organization_name: str | None,
        workspace_name: str | None,
        user_agent: str | None,
    ) -> AuthenticatedContext:
        normalized = normalize_email(email)
        if self.users.get_by_normalized_email(normalized) is not None:
            raise EmailAlreadyRegisteredError()

        password_hash = hash_password(password)

        try:
            user = self.users.create(
                email=email.strip(),
                normalized_email=normalized,
                password_hash=password_hash,
                display_name=display_name,
            )
            organization = self.organizations.create(name=organization_name or f"{display_name}'s Organization")
            workspace = self.workspaces.create(
                organization_id=organization.id,
                name=workspace_name or f"{display_name}'s Workspace",
            )
            membership = self.memberships.create(
                user_id=user.id, workspace_id=workspace.id, role=MembershipRole.OWNER
            )
            auth_session, raw_token = self._issue_session(user, user_agent=user_agent)
            self.session.commit()
        except IntegrityError:
            # A race between the pre-check above and the INSERT (two
            # concurrent registrations for the same email) surfaces here
            # as a unique-constraint violation, not a Python-level check
            # failure — map it to the same stable error either way.
            self.session.rollback()
            raise EmailAlreadyRegisteredError() from None

        return AuthenticatedContext(user=user, workspace=workspace, membership=membership, session_token=raw_token)

    def login(self, *, email: str, password: str, user_agent: str | None) -> AuthenticatedContext:
        normalized = normalize_email(email)
        user = self.users.get_by_normalized_email(normalized)

        if user is None:
            verify_against_dummy_hash(password)
            raise InvalidCredentialsError()

        if not verify_password(password, user.password_hash):
            raise InvalidCredentialsError()

        if user.status is not UserStatus.ACTIVE:
            # Deliberately the same generic error as "wrong password" —
            # a disabled account must not be distinguishable from an
            # incorrect password by an unauthenticated caller.
            raise InvalidCredentialsError()

        membership = self.memberships.get_first_active_for_user(user.id)
        if membership is None:  # pragma: no cover - cannot happen via register(), defensive only
            raise InvalidCredentialsError()
        workspace = self.workspaces.get_by_id(membership.workspace_id)
        assert workspace is not None  # a Membership always references a real Workspace

        auth_session, raw_token = self._issue_session(user, user_agent=user_agent)
        self.session.commit()

        return AuthenticatedContext(user=user, workspace=workspace, membership=membership, session_token=raw_token)

    def logout(self, auth_session: AuthSession) -> None:
        auth_session.revoked_at = datetime.now(timezone.utc)
        self.session.commit()

    def _issue_session(self, user: User, *, user_agent: str | None) -> tuple[AuthSession, str]:
        raw_token = generate_random_token()
        csrf_secret = generate_random_token()
        now = datetime.now(timezone.utc)
        auth_session = self.auth_sessions.create(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            csrf_secret=csrf_secret,
            created_at=now,
            expires_at=now + timedelta(seconds=self.settings.SESSION_TTL_SECONDS),
            user_agent_summary=_summarize_user_agent(user_agent),
        )
        return auth_session, raw_token
