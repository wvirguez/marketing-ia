"""Minimal structured (JSON) logging configuration.

Deliberately does not depend on a third-party logging library — only the
standard library ``logging`` module. Log records never include request
bodies, headers, cookies, or credentials; the access-log middleware
(``app.core.middleware``) only ever attaches method/path/status/duration/
request_id as structured fields.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_RESERVED_RECORD_KEYS = frozenset(
    logging.LogRecord(name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None).__dict__.keys()
)


class JsonLogFormatter(logging.Formatter):
    """Renders one JSON object per log line: timestamp, level, logger,
    message, plus any structured ``extra=`` fields (e.g. request_id,
    method, path, status_code, duration_ms)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_KEYS and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(*, debug: bool) -> None:
    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    # Our own "impulso.access" logger (see app.core.middleware) is the
    # source of truth for per-request access lines; keep uvicorn's built-in
    # access logger quiet to avoid duplicate, unstructured lines.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
