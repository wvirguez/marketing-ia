"""Workspaces API surface. ``GET /current`` (BACKEND-04) plus, since
BACKEND-12, ``GET``/``PATCH /{workspace_public_id}/settings`` for the
Workspace profile + AIPreference + NotificationPreference sections.

Settings routes resolve the workspace by the public id named in the URL
(the tenant-isolation gate in ``app/workspaces/service.py::
WorkspaceAccessService.get_authorized_membership``), not from
``get_current_workspace``'s "caller's own first-membership" heuristic —
BACKEND-01's own API map names this route with an explicit
``{id}`` path segment.

Mixed-section PATCH authorization (BACKEND-12 Freeze Repair §7/§24): every
submitted top-level section is authorized BEFORE ``WorkspaceSettingsService``
is invoked at all — an unauthorized request never reaches the service, so
there is no code path that could apply one section's mutation while
rejecting another's.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, get_current_workspace, require_csrf
from app.core.api_errors import ForbiddenError
from app.persistence.session import get_db
from app.users.models import User
from app.workspaces.models import MembershipRole, Workspace
from app.workspaces.schemas import (
    WorkspacePublic,
    WorkspaceSettingsPatchRequest,
    WorkspaceSettingsResponse,
    workspace_settings_to_public,
    workspace_to_public,
)
from app.workspaces.service import UNSET, WorkspaceAccessService, WorkspaceSettingsService

router = APIRouter(tags=["workspaces"])

_ADMIN_ROLES = (MembershipRole.OWNER, MembershipRole.ADMIN)


@router.get("/current", response_model=WorkspacePublic)
async def get_current_workspace_route(workspace: Workspace = Depends(get_current_workspace)) -> WorkspacePublic:
    return workspace_to_public(workspace)


@router.get("/{workspace_public_id}/settings", response_model=WorkspaceSettingsResponse)
async def get_workspace_settings(
    workspace_public_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceSettingsResponse:
    workspace, _membership = WorkspaceAccessService(db).get_authorized_membership(
        user_id=user.id, workspace_public_id=workspace_public_id
    )
    ai_preference, notification_preference = WorkspaceSettingsService(db).get_settings(
        workspace=workspace, user_id=user.id
    )
    return workspace_settings_to_public(workspace, ai_preference, notification_preference)


@router.patch("/{workspace_public_id}/settings", response_model=WorkspaceSettingsResponse, dependencies=[Depends(require_csrf)])
async def patch_workspace_settings(
    workspace_public_id: str,
    payload: WorkspaceSettingsPatchRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceSettingsResponse:
    workspace, membership = WorkspaceAccessService(db).get_authorized_membership(
        user_id=user.id, workspace_public_id=workspace_public_id
    )

    submitted_sections = payload.model_fields_set
    # `workspace`/`ai_preferences` are Admin-gated; `notifications` is
    # self-service for any active member. Every submitted section is
    # checked here, BEFORE any mutation is attempted — a caller lacking
    # authority for even one submitted section gets a 403 for the whole
    # request, with nothing persisted (BACKEND-12 Freeze Repair §7).
    if ({"workspace", "ai_preferences"} & submitted_sections) and membership.role not in _ADMIN_ROLES:
        raise ForbiddenError()

    workspace_name = payload.workspace.name if payload.workspace is not None else UNSET

    ai_fields = payload.ai_preferences.model_fields_set if payload.ai_preferences is not None else set()
    tone = payload.ai_preferences.tone if "tone" in ai_fields else UNSET
    depth = payload.ai_preferences.depth if "depth" in ai_fields else UNSET
    creativity = payload.ai_preferences.creativity if "creativity" in ai_fields else UNSET

    notifications: dict[str, bool] | None = None
    if payload.notifications is not None:
        notif_fields = payload.notifications.model_fields_set
        if notif_fields:
            notifications = {key: getattr(payload.notifications, key) for key in notif_fields}

    workspace, ai_preference, notification_preference = WorkspaceSettingsService(db).patch_settings(
        workspace=workspace,
        actor_user_id=user.id,
        workspace_name=workspace_name,
        tone=tone,
        depth=depth,
        creativity=creativity,
        notifications=notifications,
    )
    return workspace_settings_to_public(workspace, ai_preference, notification_preference)
