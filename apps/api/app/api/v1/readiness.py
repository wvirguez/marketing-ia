"""Readiness is a stronger claim than `/health`'s pure process liveness:
it answers "can this instance currently serve requests that need the
database." The two must never be conflated — see BACKEND-03 §13.

Never includes hostnames, usernames, DSNs, passwords, or raw SQL error
text in the response; a boolean-shaped status is all a client needs.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.persistence.session import check_database_ready

router = APIRouter(tags=["readiness"])


@router.get("/readiness")
async def readiness() -> JSONResponse:
    if check_database_ready():
        return JSONResponse(status_code=200, content={"status": "ready", "database": "ok"})
    return JSONResponse(status_code=503, content={"status": "not_ready", "database": "unavailable"})
