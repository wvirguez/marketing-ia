"""Multi-tenant safety for the campaign domain (BACKEND-05 §10): User A
cannot access Campaign B merely by knowing its public ID.

Real database required — this proves the actual SQL-backed authorization
check, not a mock.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.campaignstest import campaign_payload, register_and_get_csrf

pytestmark = pytest.mark.postgres


@pytest.fixture()
def two_users_two_campaigns(auth_client: TestClient) -> dict:
    client_a = auth_client
    csrf_a = register_and_get_csrf(client_a, display_name="User A")
    campaign_a = client_a.post(
        "/api/v1/campaigns", json=campaign_payload(name="Campaign A"), headers={"X-CSRF-Token": csrf_a}
    ).json()["campaign"]["id"]

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    csrf_b = register_and_get_csrf(client_b, display_name="User B")
    campaign_b = client_b.post(
        "/api/v1/campaigns", json=campaign_payload(name="Campaign B"), headers={"X-CSRF-Token": csrf_b}
    ).json()["campaign"]["id"]

    return {
        "client_a": client_a,
        "csrf_a": csrf_a,
        "campaign_a": campaign_a,
        "client_b": client_b,
        "csrf_b": csrf_b,
        "campaign_b": campaign_b,
    }


def test_a_can_access_a(two_users_two_campaigns: dict) -> None:
    fixtures = two_users_two_campaigns
    response = fixtures["client_a"].get(f"/api/v1/campaigns/{fixtures['campaign_a']}")
    assert response.status_code == 200


def test_b_can_access_b(two_users_two_campaigns: dict) -> None:
    fixtures = two_users_two_campaigns
    response = fixtures["client_b"].get(f"/api/v1/campaigns/{fixtures['campaign_b']}")
    assert response.status_code == 200


def test_a_cannot_access_b(two_users_two_campaigns: dict) -> None:
    fixtures = two_users_two_campaigns
    response = fixtures["client_a"].get(f"/api/v1/campaigns/{fixtures['campaign_b']}")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_b_cannot_access_a(two_users_two_campaigns: dict) -> None:
    fixtures = two_users_two_campaigns
    response = fixtures["client_b"].get(f"/api/v1/campaigns/{fixtures['campaign_a']}")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_a_cannot_patch_b(two_users_two_campaigns: dict) -> None:
    fixtures = two_users_two_campaigns
    response = fixtures["client_a"].patch(
        f"/api/v1/campaigns/{fixtures['campaign_b']}",
        json={"name": "Hijacked"},
        headers={"X-CSRF-Token": fixtures["csrf_a"]},
    )
    assert response.status_code == 403


def test_a_cannot_archive_b(two_users_two_campaigns: dict) -> None:
    fixtures = two_users_two_campaigns
    response = fixtures["client_a"].post(
        f"/api/v1/campaigns/{fixtures['campaign_b']}/archive", headers={"X-CSRF-Token": fixtures["csrf_a"]}
    )
    assert response.status_code == 403


def test_a_cannot_list_bs_runs(two_users_two_campaigns: dict) -> None:
    fixtures = two_users_two_campaigns
    response = fixtures["client_a"].get(f"/api/v1/campaigns/{fixtures['campaign_b']}/runs")
    assert response.status_code == 403


def test_a_does_not_see_b_in_their_own_listing(two_users_two_campaigns: dict) -> None:
    fixtures = two_users_two_campaigns
    body = fixtures["client_a"].get("/api/v1/campaigns").json()
    ids = {item["id"] for item in body["items"]}
    assert fixtures["campaign_a"] in ids
    assert fixtures["campaign_b"] not in ids


def test_unknown_campaign_public_id_is_indistinguishable_from_not_yours(two_users_two_campaigns: dict) -> None:
    fixtures = two_users_two_campaigns
    response = fixtures["client_a"].get("/api/v1/campaigns/CMP-TOTALLYFAKE0")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
