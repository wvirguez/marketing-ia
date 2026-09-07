"""Request correlation ID + structured access logging.

The request ID is a correlation aid only. It is never treated as an
authentication or authorization signal anywhere in this codebase.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

access_logger = logging.getLogger("impulso.access")


def _new_request_id() -> str:
    return uuid.uuid4().hex


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns/propagates ``X-Request-ID`` and logs one structured access
    line per request (method, path, status code, duration, request id).

    Never logs headers, cookies, or request/response bodies.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming if incoming and _SAFE_REQUEST_ID.match(incoming) else _new_request_id()
        request.state.request_id = request_id

        started_at = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # An exception that reaches here means no registered handler
            # inside the routing/ExceptionMiddleware layer caught it — it
            # is propagating up to Starlette's ServerErrorMiddleware,
            # which sits *above* this middleware and owns translating it
            # into the actual HTTP response (see app.core.errors). This
            # middleware must not swallow it or produce its own response
            # (that would create a second, competing 500 response) — its
            # only remaining responsibility on this path is to make sure
            # the failure is still represented in the access log with the
            # correct status/duration, since the normal post-call_next
            # logging below is unreachable once an exception has escaped.
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            access_logger.info(
                "request completed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": 500,
                    "duration_ms": duration_ms,
                },
            )
            raise

        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id
        access_logger.info(
            "request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
