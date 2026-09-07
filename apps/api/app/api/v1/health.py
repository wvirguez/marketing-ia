from __future__ import annotations

from fastapi import APIRouter

from app.shared.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_v1() -> HealthResponse:
    """Process liveness only, under the versioned prefix. Does not check
    database, AI provider, or external integration connectivity — none
    exist yet."""
    return HealthResponse(status="ok", service="impulso-api")
