"""Proves the real HTTP failure path end-to-end:

    HTTP REQUEST -> RequestContextMiddleware -> route raises an
    unexpected exception -> Starlette's ServerErrorMiddleware ->
    app.core.errors.unhandled_exception_handler -> HTTP RESPONSE

This deliberately does NOT call the exception-handler function directly
(that only proves the function's own logic, not what actually happens
when an exception has to travel back out through the whole middleware
stack — which is exactly where BACKEND-02R found and fixed a real gap:
`X-Request-ID` was missing from genuine 500 responses, and no access-log
line was ever written for them).

The crashing route lives ONLY in this test module, on a throwaway
FastAPI instance built with the exact same production wiring
(`app.main.configure_app`). It never touches the real `app` object in
`app.main`, and it is never part of the production OpenAPI schema.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.middleware import REQUEST_ID_HEADER
from app.main import configure_app

_CRASH_PATH = "/__test_only__/crash"
_SECRET_DETAIL = "sensitive internal failure"


def _build_failure_test_app() -> FastAPI:
    settings = get_settings()
    test_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    configure_app(test_app, settings)

    @test_app.get(_CRASH_PATH, include_in_schema=False)
    async def crash() -> None:
        raise RuntimeError(_SECRET_DETAIL)

    return test_app


@pytest.fixture()
def failure_client() -> TestClient:
    # raise_server_exceptions=False is required: by default TestClient
    # re-raises the endpoint's exception into the test process, which
    # would prove nothing about how the real ASGI stack turns it into an
    # HTTP response. With it False, the client receives whatever actual
    # HTTP response the app produced — the same thing a real browser or
    # the frontend would receive from a live server.
    return TestClient(_build_failure_test_app(), raise_server_exceptions=False)


def test_crash_route_does_not_exist_on_the_production_app() -> None:
    from app.main import app as production_app

    production_client = TestClient(production_app, raise_server_exceptions=False)
    response = production_client.get(_CRASH_PATH)

    # The test-only route was added to an isolated FastAPI instance built
    # in this module, never to the shared `app` singleton — hitting the
    # same path on the real app must behave like any other unknown route.
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_crash_route_is_not_in_production_openapi() -> None:
    from app.main import app as production_app

    assert _CRASH_PATH not in production_app.openapi()["paths"]


def test_unhandled_exception_returns_500_with_safe_envelope(failure_client: TestClient) -> None:
    response = failure_client.get(_CRASH_PATH)

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert body["error"]["message"] == "An unexpected error occurred."
    assert _SECRET_DETAIL not in response.text


def test_unhandled_exception_includes_request_id_in_envelope(failure_client: TestClient) -> None:
    body = failure_client.get(_CRASH_PATH).json()
    assert body["error"]["request_id"]


def test_unhandled_exception_sets_request_id_header(failure_client: TestClient) -> None:
    response = failure_client.get(_CRASH_PATH)
    assert REQUEST_ID_HEADER in response.headers
    assert response.headers[REQUEST_ID_HEADER]


def test_unhandled_exception_header_matches_envelope_request_id(failure_client: TestClient) -> None:
    response = failure_client.get(_CRASH_PATH)
    assert response.headers[REQUEST_ID_HEADER] == response.json()["error"]["request_id"]


def test_unhandled_exception_preserves_safe_client_request_id(failure_client: TestClient) -> None:
    response = failure_client.get(_CRASH_PATH, headers={REQUEST_ID_HEADER: "client-supplied-id"})
    assert response.headers[REQUEST_ID_HEADER] == "client-supplied-id"
    assert response.json()["error"]["request_id"] == "client-supplied-id"


def test_unhandled_exception_replaces_unsafe_client_request_id(failure_client: TestClient) -> None:
    unsafe = "not a safe id! <script>"
    response = failure_client.get(_CRASH_PATH, headers={REQUEST_ID_HEADER: unsafe})
    assert response.headers[REQUEST_ID_HEADER] != unsafe
    assert response.json()["error"]["request_id"] != unsafe


def test_unhandled_exception_produces_one_structured_access_log_line(
    failure_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="impulso.access"):
        response = failure_client.get(_CRASH_PATH)

    access_records = [r for r in caplog.records if r.name == "impulso.access"]
    assert len(access_records) == 1

    record = access_records[0]
    assert record.request_id == response.headers[REQUEST_ID_HEADER]
    assert record.method == "GET"
    assert record.path == _CRASH_PATH
    assert record.status_code == 500
    assert record.duration_ms >= 0

    # The access log line itself must never carry the exception text.
    assert _SECRET_DETAIL not in record.getMessage()


def test_unhandled_exception_is_logged_exactly_once_by_error_logger(
    failure_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="impulso.errors"):
        failure_client.get(_CRASH_PATH)

    error_records = [r for r in caplog.records if r.name == "impulso.errors"]
    assert len(error_records) == 1
    # The traceback is allowed (expected) here — this is the server-side
    # log, not anything sent to the client.
    assert error_records[0].exc_info is not None
