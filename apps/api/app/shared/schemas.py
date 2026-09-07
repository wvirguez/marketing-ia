"""Response models shared across API modules.

Kept intentionally small in this stage — only what the foundation
endpoints and the error envelope need. Domain-specific schemas belong in
their own future module (``app/campaigns/schemas.py``, etc.), not here.
"""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str


class RootResponse(BaseModel):
    service: str
    api_version: str
    status: str


class ErrorDetail(BaseModel):
    field: str | None = None
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
