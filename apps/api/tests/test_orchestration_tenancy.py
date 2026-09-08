"""Multi-tenant safety for the orchestration domain (BACKEND-06 §24):
User A cannot read or mutate anything belonging to User B's campaign
run merely by knowing its public ID. Real database required.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.orchestrationtest import initialize_run, run_path, start_run

pytestmark = pytest.mark.postgres


@pytest.fixture()
def two_users_two_runs(auth_client: TestClient) -> dict:
    client_a = auth_client
    csrf_a = register_and_get_csrf(client_a, display_name="User A")
    body_a = client_a.post(
        "/api/v1/campaigns", json=campaign_payload(name="Campaign A"), headers={"X-CSRF-Token": csrf_a}
    ).json()

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    csrf_b = register_and_get_csrf(client_b, display_name="User B")
    body_b = client_b.post(
        "/api/v1/campaigns", json=campaign_payload(name="Campaign B"), headers={"X-CSRF-Token": csrf_b}
    ).json()

    fixtures_a = {
        "client": client_a,
        "csrf_token": csrf_a,
        "campaign_id": body_a["campaign"]["id"],
        "run_id": body_a["run"]["id"],
    }
    initialize_run(fixtures_a)
    start_run(fixtures_a)

    return {
        "a": fixtures_a,
        "b": {
            "client": client_b,
            "csrf_token": csrf_b,
            "campaign_id": body_b["campaign"]["id"],
            "run_id": body_b["run"]["id"],
        },
    }


def test_a_can_read_own_run(two_users_two_runs: dict) -> None:
    fixtures = two_users_two_runs["a"]
    assert fixtures["client"].get(run_path(fixtures)).status_code == 200


def test_a_cannot_read_bs_run(two_users_two_runs: dict) -> None:
    fixtures = dict(two_users_two_runs["b"], client=two_users_two_runs["a"]["client"])
    response = fixtures["client"].get(run_path(fixtures))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_a_cannot_initialize_bs_run(two_users_two_runs: dict) -> None:
    a, b = two_users_two_runs["a"], two_users_two_runs["b"]
    forged = dict(b, client=a["client"], csrf_token=a["csrf_token"])
    response = forged["client"].post(run_path(forged, "/initialize"), headers={"X-CSRF-Token": forged["csrf_token"]})
    assert response.status_code == 403


def test_a_cannot_start_bs_run(two_users_two_runs: dict) -> None:
    a, b = two_users_two_runs["a"], two_users_two_runs["b"]
    forged = dict(b, client=a["client"], csrf_token=a["csrf_token"])
    response = forged["client"].post(run_path(forged, "/start"), headers={"X-CSRF-Token": forged["csrf_token"]})
    assert response.status_code == 403


def test_a_cannot_see_bs_progress(two_users_two_runs: dict) -> None:
    a, b = two_users_two_runs["a"], two_users_two_runs["b"]
    forged = dict(b, client=a["client"])
    response = forged["client"].get(run_path(forged, "/progress"))
    assert response.status_code == 403


def test_a_cannot_see_bs_stages(two_users_two_runs: dict) -> None:
    a, b = two_users_two_runs["a"], two_users_two_runs["b"]
    forged = dict(b, client=a["client"])
    assert forged["client"].get(run_path(forged, "/stages")).status_code == 403


def test_a_cannot_see_bs_events(two_users_two_runs: dict) -> None:
    a, b = two_users_two_runs["a"], two_users_two_runs["b"]
    forged = dict(b, client=a["client"])
    assert forged["client"].get(run_path(forged, "/events")).status_code == 403


def test_a_cannot_see_bs_decisions(two_users_two_runs: dict) -> None:
    a, b = two_users_two_runs["a"], two_users_two_runs["b"]
    forged = dict(b, client=a["client"])
    assert forged["client"].get(run_path(forged, "/decisions")).status_code == 403


def test_unknown_run_public_id_is_indistinguishable_from_not_yours(two_users_two_runs: dict) -> None:
    fixtures = two_users_two_runs["a"]
    forged = dict(fixtures, run_id="RUN-TOTALLYFAKE0")
    response = forged["client"].get(run_path(forged))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_run_id_from_a_different_campaign_is_rejected(two_users_two_runs: dict) -> None:
    """B's run id is real, but paired with A's campaign id in the URL —
    must be rejected exactly like a nonexistent id, not silently
    resolved through the run alone."""
    a, b = two_users_two_runs["a"], two_users_two_runs["b"]
    mismatched = {"client": a["client"], "campaign_id": a["campaign_id"], "run_id": b["run_id"]}
    response = mismatched["client"].get(run_path(mismatched))
    assert response.status_code == 403
