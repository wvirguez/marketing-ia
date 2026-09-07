from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.core.middleware import REQUEST_ID_HEADER

_SAFE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def test_generates_request_id_when_absent(client: TestClient) -> None:
    response = client.get("/")
    assert REQUEST_ID_HEADER in response.headers
    assert _SAFE.match(response.headers[REQUEST_ID_HEADER])


def test_echoes_provided_safe_request_id(client: TestClient) -> None:
    response = client.get("/", headers={REQUEST_ID_HEADER: "abc-123.request"})
    assert response.headers[REQUEST_ID_HEADER] == "abc-123.request"


def test_replaces_unsafe_request_id(client: TestClient) -> None:
    unsafe = "not a safe id! <script>"
    response = client.get("/", headers={REQUEST_ID_HEADER: unsafe})
    assert response.headers[REQUEST_ID_HEADER] != unsafe
    assert _SAFE.match(response.headers[REQUEST_ID_HEADER])


def test_request_id_is_not_treated_as_authority(client: TestClient) -> None:
    """A request ID must never grant access to anything — there is no
    protected resource yet, but this documents the intended contract:
    an arbitrary client-supplied request id must not change the response
    status of an otherwise-identical request."""
    baseline = client.get("/health")
    with_custom_id = client.get("/health", headers={REQUEST_ID_HEADER: "totally-arbitrary-value"})
    assert baseline.status_code == with_custom_id.status_code == 200
    assert baseline.json() == with_custom_id.json()
