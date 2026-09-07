"""Consistent API error envelope + exception handlers.

Every error response has the shape::

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": [...]}}

Client-facing messages never include a stack trace or internal exception
text. In development, the underlying traceback is still written to the
server-side log via ``logger.exception``.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from app.core.middleware import REQUEST_ID_HEADER
from app.shared.schemas import ErrorBody, ErrorDetail, ErrorResponse

logger = logging.getLogger("impulso.errors")

# FastAPI/Starlette renamed HTTP_422_UNPROCESSABLE_ENTITY to
# HTTP_422_UNPROCESSABLE_CONTENT for RFC alignment; the old name is kept
# as a deprecated alias in newer versions but does not exist in every
# version within our declared dependency range, so resolve it defensively
# without ever touching the deprecated attribute when the new one exists.
_HTTP_422 = 422

_HTTP_ERROR_CODES: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
    status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
    status.HTTP_403_FORBIDDEN: "FORBIDDEN",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
    status.HTTP_409_CONFLICT: "CONFLICT",
    _HTTP_422: "VALIDATION_ERROR",
}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _envelope(
    code: str,
    message: str,
    request_id: str | None,
    details: list[ErrorDetail] | None = None,
) -> dict:
    body = ErrorBody(code=code, message=message, request_id=request_id, details=details)
    return ErrorResponse(error=body).model_dump(exclude_none=True)


def _error_response(status_code: int, content: dict, request_id: str | None, headers: dict | None = None) -> JSONResponse:
    """Builds the JSONResponse for an error and stamps ``X-Request-ID`` on
    it directly.

    This does not depend on `RequestContextMiddleware` running again on
    the way out. For a `StarletteHTTPException`/`RequestValidationError`
    it normally would (they're handled below `RequestContextMiddleware`,
    inside Starlette's `ExceptionMiddleware`, so the response flows back
    through the middleware's own header injection). But a bare
    `Exception` handler is hoisted by Starlette into `ServerErrorMiddleware`,
    which sits *above* `RequestContextMiddleware` — the exception has
    already escaped past that middleware by the time this handler runs,
    so nothing downstream will add the header for us. Setting it here,
    uniformly for all three handlers, makes the contract hold regardless
    of which internal layer ends up calling the handler.
    """
    response = JSONResponse(status_code=status_code, content=content, headers=headers)
    if request_id:
        response.headers[REQUEST_ID_HEADER] = request_id
    return response


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_ERROR_CODES.get(exc.status_code, "HTTP_ERROR")
    message = exc.detail if isinstance(exc.detail, str) else "Request could not be processed."
    request_id = _request_id(request)
    return _error_response(
        exc.status_code,
        _envelope(code, message, request_id),
        request_id,
        headers=getattr(exc, "headers", None),
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        ErrorDetail(field=".".join(str(part) for part in error["loc"]), message=error["msg"])
        for error in exc.errors()
    ]
    request_id = _request_id(request)
    return _error_response(
        _HTTP_422,
        _envelope("VALIDATION_ERROR", "One or more fields are invalid.", request_id, details),
        request_id,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception while processing request", exc_info=exc)
    request_id = _request_id(request)
    return _error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        _envelope("INTERNAL_SERVER_ERROR", "An unexpected error occurred.", request_id),
        request_id,
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
