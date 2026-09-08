"""Shared helpers/fixtures for orchestration tests — real database
required."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.campaignstest import campaign_payload, register_and_get_csrf


@pytest.fixture()
def campaign_run_client(auth_client: TestClient) -> Iterator[dict]:
    """Registers a fresh user, creates a campaign (with its atomic
    Run #1), and returns everything an orchestration test needs:
    ``{"client", "csrf_token", "campaign_id", "run_id"}``."""
    csrf_token = register_and_get_csrf(auth_client)
    body = auth_client.post(
        "/api/v1/campaigns", json=campaign_payload(), headers={"X-CSRF-Token": csrf_token}
    ).json()
    yield {
        "client": auth_client,
        "csrf_token": csrf_token,
        "campaign_id": body["campaign"]["id"],
        "run_id": body["run"]["id"],
    }


def run_path(fixtures: dict, suffix: str = "") -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/runs/{fixtures['run_id']}{suffix}"


def initialize_run(fixtures: dict) -> dict:
    response = fixtures["client"].post(run_path(fixtures, "/initialize"), headers={"X-CSRF-Token": fixtures["csrf_token"]})
    assert response.status_code == 200, response.text
    return response.json()


def start_run(fixtures: dict) -> dict:
    response = fixtures["client"].post(run_path(fixtures, "/start"), headers={"X-CSRF-Token": fixtures["csrf_token"]})
    assert response.status_code == 200, response.text
    return response.json()
