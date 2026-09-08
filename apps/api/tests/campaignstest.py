"""Shared helpers/fixtures for campaign tests — real database required."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.authtest import register_payload


def campaign_payload(**overrides: object) -> dict:
    payload = {
        "name": "Método Canino en Casa",
        "prompt": "Quiero lanzar un curso sobre adiestramiento canino en casa para personas sin experiencia previa.",
    }
    payload.update(overrides)
    return payload


def register_and_get_csrf(client: TestClient, **overrides: object) -> str:
    client.post("/api/v1/auth/register", json=register_payload(**overrides))
    return client.get("/api/v1/auth/csrf").json()["csrf_token"]


@pytest.fixture()
def campaign_client(auth_client: TestClient) -> Iterator[dict]:
    """A freshly-registered, CSRF-armed client — everything a campaign
    mutation test needs, in the shape `{"client": ..., "csrf_token": ...}`."""
    csrf_token = register_and_get_csrf(auth_client)
    yield {"client": auth_client, "csrf_token": csrf_token}
