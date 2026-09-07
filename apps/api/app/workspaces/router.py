from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.dependencies import get_current_workspace
from app.workspaces.models import Workspace
from app.workspaces.schemas import WorkspacePublic, workspace_to_public

router = APIRouter(tags=["workspaces"])


@router.get("/current", response_model=WorkspacePublic)
async def get_current_workspace_route(workspace: Workspace = Depends(get_current_workspace)) -> WorkspacePublic:
    return workspace_to_public(workspace)
