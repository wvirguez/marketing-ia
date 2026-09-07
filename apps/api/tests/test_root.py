from __future__ import annotations

from fastapi.testclient import TestClient


def test_root_returns_service_identity_only(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "service": "Impulso API",
        "api_version": "v1",
        "status": "development",
    }
    # No business data of any kind belongs in the root identity payload.
    assert set(body.keys()) == {"service", "api_version", "status"}
