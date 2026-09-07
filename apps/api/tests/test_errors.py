from __future__ import annotations

import asyncio
import json

from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.errors import unhandled_exception_handler, validation_exception_handler


def _fake_request(request_id: str = "test-request-id") -> Request:
    request = Request(scope={"type": "http", "method": "GET", "path": "/x", "headers": []})
    request.state.request_id = request_id
    return request


def test_unknown_route_returns_error_envelope(client: TestClient) -> None:
    response = client.get("/this-route-does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["request_id"]  # always present once middleware has run


def test_method_not_allowed_uses_error_envelope(client: TestClient) -> None:
    response = client.post("/health")
    assert response.status_code == 405
    body = response.json()
    assert body["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_validation_error_handler_shape() -> None:
    request = _fake_request()
    exc = RequestValidationError(
        errors=[{"loc": ("body", "email"), "msg": "field required", "type": "missing"}]
    )

    response = asyncio.run(validation_exception_handler(request, exc))

    assert response.status_code == 422
    body = json.loads(response.body)
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["request_id"] == "test-request-id"
    assert body["error"]["details"] == [{"field": "body.email", "message": "field required"}]


def test_unhandled_exception_never_leaks_details_to_client() -> None:
    request = _fake_request()
    secret_detail = "super secret internal stack detail"

    response = asyncio.run(unhandled_exception_handler(request, ValueError(secret_detail)))

    assert response.status_code == 500
    raw = response.body.decode()
    assert secret_detail not in raw
    body = json.loads(raw)
    assert body["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert body["error"]["message"] == "An unexpected error occurred."
