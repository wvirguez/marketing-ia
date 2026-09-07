"""Aggregates all routers mounted under the versioned API prefix
(``settings.API_V1_PREFIX``, centralized in ``app.core.config`` — never
repeated as a string literal in individual route modules).

Future domain routers (campaigns, content, metrics, settings, ...) will
be included here as they are implemented, one line each.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import health, readiness

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
api_v1_router.include_router(readiness.router)
