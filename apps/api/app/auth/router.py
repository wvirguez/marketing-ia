from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.auth.cookies import clear_session_cookie, set_session_cookie
from app.auth.dependencies import get_current_membership, get_current_session, get_current_user, get_current_workspace, require_csrf
from app.auth.models import AuthSession
from app.auth.schemas import CsrfTokenResponse, LoginRequest, LogoutResponse, RegisterRequest, SessionContext
from app.auth.service import AuthService
from app.core.config import Settings, get_settings
from app.persistence.session import get_db
from app.users.models import User
from app.users.repository import UserPreferenceRepository
from app.users.schemas import user_to_public
from app.workspaces.models import Membership, Workspace
from app.workspaces.schemas import membership_to_public, workspace_to_public

router = APIRouter(tags=["auth"])


@router.post("/register", response_model=SessionContext, status_code=201)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionContext:
    context = AuthService(db, settings=settings).register(
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
        organization_name=payload.organization_name,
        workspace_name=payload.workspace_name,
        user_agent=request.headers.get("user-agent"),
    )
    set_session_cookie(response, token=context.session_token, settings=settings)
    return SessionContext(
        # A brand-new registration can never already have a UserPreference
        # row (BACKEND-12: created lazily, only on a Settings PATCH).
        user=user_to_public(context.user, preference=None),
        workspace=workspace_to_public(context.workspace),
        membership=membership_to_public(context.membership),
    )


@router.post("/login", response_model=SessionContext)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionContext:
    context = AuthService(db, settings=settings).login(
        email=payload.email,
        password=payload.password,
        user_agent=request.headers.get("user-agent"),
    )
    set_session_cookie(response, token=context.session_token, settings=settings)
    preference = UserPreferenceRepository(db).get_by_user_id(context.user.id)
    return SessionContext(
        user=user_to_public(context.user, preference=preference),
        workspace=workspace_to_public(context.workspace),
        membership=membership_to_public(context.membership),
    )


@router.post("/logout", response_model=LogoutResponse, dependencies=[Depends(require_csrf)])
async def logout(
    response: Response,
    auth_session: AuthSession = Depends(get_current_session),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LogoutResponse:
    AuthService(db, settings=settings).logout(auth_session)
    clear_session_cookie(response, settings=settings)
    return LogoutResponse()


@router.get("/session", response_model=SessionContext)
async def current_session(
    user: User = Depends(get_current_user),
    membership: Membership = Depends(get_current_membership),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> SessionContext:
    preference = UserPreferenceRepository(db).get_by_user_id(user.id)
    return SessionContext(
        user=user_to_public(user, preference=preference),
        workspace=workspace_to_public(workspace),
        membership=membership_to_public(membership),
    )


@router.get("/csrf", response_model=CsrfTokenResponse)
async def csrf_token(auth_session: AuthSession = Depends(get_current_session)) -> CsrfTokenResponse:
    return CsrfTokenResponse(csrf_token=auth_session.csrf_secret)
