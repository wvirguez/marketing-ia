from __future__ import annotations

from fastapi.testclient import TestClient

EXPECTED_BODY = {"status": "ok", "service": "impulso-api"}


def test_health_unversioned(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == EXPECTED_BODY


def test_health_v1(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == EXPECTED_BODY


def test_health_never_claims_unbuilt_dependencies(client: TestClient) -> None:
    """Health means process liveness only — there is no database, AI
    provider, or external integration to report on yet."""
    body = client.get("/health").json()
    forbidden_keys = {"database", "db", "ai", "agents", "integrations"}
    assert forbidden_keys.isdisjoint(body.keys())
