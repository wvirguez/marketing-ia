"""Aggregates all routers mounted under the versioned API prefix
(``settings.API_V1_PREFIX``, centralized in ``app.core.config`` — never
repeated as a string literal in individual route modules).

Future domain routers (campaigns, content, metrics, settings, ...) will
be included here as they are implemented, one line each.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import health, readiness
from app.auth.router import router as auth_router
from app.campaigns.router import router as campaigns_router
from app.orchestration.router import router as orchestration_router
from app.users.router import router as users_router
from app.workspaces.router import router as workspaces_router

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
api_v1_router.include_router(readiness.router)
api_v1_router.include_router(auth_router, prefix="/auth")
api_v1_router.include_router(users_router, prefix="/users")
api_v1_router.include_router(workspaces_router, prefix="/workspaces")
api_v1_router.include_router(campaigns_router, prefix="/campaigns")
# Own full path prefix already includes /campaigns/{id}/runs/{id} — see
# app/orchestration/router.py's module docstring for why this is
# mounted separately from campaigns_router rather than nested inside it.
api_v1_router.include_router(orchestration_router)
