from __future__ import annotations

from fastapi.testclient import TestClient


def test_cors_preflight_allows_configured_local_origin(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={
            "origin": "http://localhost:3000",
            "access-control-request-method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_cors_rejects_unlisted_origin(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={
            "origin": "http://not-allowed.example.com",
            "access-control-request-method": "GET",
        },
    )
    assert "access-control-allow-origin" not in response.headers
