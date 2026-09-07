"""Unversioned root-level endpoints: service identity and process health.

Neither endpoint depends on a database, an AI provider, or any external
integration — none exist yet. Health here means "the application process
is running and can respond," nothing more.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.shared.schemas import HealthResponse, RootResponse

router = APIRouter()


@router.get("/", response_model=RootResponse, tags=["root"])
async def root(settings: Settings = Depends(get_settings)) -> RootResponse:
    api_version = settings.API_V1_PREFIX.rsplit("/", maxsplit=1)[-1]
    return RootResponse(service=settings.APP_NAME, api_version=api_version, status=settings.APP_ENV)


@router.get("/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service="impulso-api")
