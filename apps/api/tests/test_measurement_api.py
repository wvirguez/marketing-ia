"""API contract, tenancy, idempotency, and security tests for the
Measurement API (BACKEND-11 §10-§15). All marked `postgres`.

Unlike every other bounded context in this review series, Metric Entry
writes ARE public — POST/PUT are tested directly through the HTTP client,
not only via a service-layer helper.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from tests.campaignstest import campaign_payload, register_and_get_csrf

pytestmark = pytest.mark.postgres

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _metrics_payload(**overrides: object) -> dict:
    payload = {
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
        "channel": "Instagram",
        "source": "MANUAL",
        "client_request_id": "req-http-1",
        "values": {"impressions": "1000", "clicks": "50"},
    }
    payload.update(overrides)
    return payload


# --- route surface -------------------------------------------------------


def test_only_get_post_put_exist_for_metrics(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    path = f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics"
    assert fixtures["client"].get(path).status_code == 200
    assert fixtures["client"].patch(path, json={}).status_code == 405
    assert fixtures["client"].delete(path).status_code == 405


def test_only_get_exists_for_analysis(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    path = f"/api/v1/campaigns/{fixtures['campaign_id']}/analysis"
    assert fixtures["client"].get(path).status_code == 200
    assert fixtures["client"].post(path, json={}).status_code == 405
    assert fixtures["client"].put(path, json={}).status_code == 405
    assert fixtures["client"].patch(path, json={}).status_code == 405
    assert fixtures["client"].delete(path).status_code == 405


def test_no_learning_or_settings_route_exists(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/learning")
    assert response.status_code == 404


# --- POST /metrics ---------------------------------------------------------


def test_post_metrics_requires_csrf(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics", json=_metrics_payload())
    assert response.status_code == 403


def test_post_metrics_creates_entry(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics",
        json=_metrics_payload(client_request_id="req-create-1"),
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 201
    body = response.json()
    from decimal import Decimal

    assert body["id"].startswith("MET-")
    assert body["source"] == "MANUAL"
    assert {k: Decimal(v) for k, v in body["values"].items()} == {"impressions": Decimal("1000"), "clicks": Decimal("50")}
    assert body["is_current"] is True


def test_post_metrics_rejects_derived_source(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics",
        json=_metrics_payload(source="DERIVED", client_request_id="req-derived-1"),
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 422


def test_post_metrics_rejects_empty_values(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics",
        json=_metrics_payload(values={}, client_request_id="req-empty-1"),
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 422


def test_post_metrics_rejects_period_end_before_start(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics",
        json=_metrics_payload(period_start="2026-02-01", period_end="2026-01-01", client_request_id="req-badperiod-1"),
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 422


def test_post_metrics_idempotent_on_retry(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    payload = _metrics_payload(client_request_id="req-idempotent-1")
    first = fixtures["client"].post(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics", json=payload, headers={"X-CSRF-Token": fixtures["csrf_token"]}
    )
    second = fixtures["client"].post(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics", json=payload, headers={"X-CSRF-Token": fixtures["csrf_token"]}
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    listing = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics").json()
    matching = [item for item in listing["items"] if item["id"] == first.json()["id"]]
    assert len(matching) == 1


# --- PUT /metrics (append-correction) --------------------------------------


def test_put_metrics_appends_new_row_preserving_prior(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    csrf = {"X-CSRF-Token": fixtures["csrf_token"]}
    original = fixtures["client"].post(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics",
        json=_metrics_payload(client_request_id="req-put-original", values={"impressions": "1000"}),
        headers=csrf,
    )
    assert original.status_code == 201

    correction = fixtures["client"].put(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics",
        json=_metrics_payload(client_request_id="req-put-correction", values={"impressions": "1500"}),
        headers=csrf,
    )
    assert correction.status_code == 200
    assert correction.json()["id"] != original.json()["id"]

    listing = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics").json()
    ids = {item["id"]: item for item in listing["items"]}
    assert original.json()["id"] in ids
    assert correction.json()["id"] in ids
    # Prior entry's own values remain exactly as originally submitted
    # (compared numerically — the API serializes Decimal(20,4) with its
    # full scale, e.g. "1000.0000", not the original input string).
    from decimal import Decimal

    assert Decimal(ids[original.json()["id"]]["values"]["impressions"]) == Decimal("1000")
    assert ids[original.json()["id"]]["is_current"] is False
    assert ids[correction.json()["id"]]["is_current"] is True


def test_put_metrics_retry_with_same_request_id_does_not_duplicate(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    csrf = {"X-CSRF-Token": fixtures["csrf_token"]}
    payload = _metrics_payload(client_request_id="req-put-retry")
    first = fixtures["client"].put(f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics", json=payload, headers=csrf)
    second = fixtures["client"].put(f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics", json=payload, headers=csrf)
    assert first.json()["id"] == second.json()["id"]


# --- response shape / empty-safe reads --------------------------------------


def test_empty_metrics_list_for_a_campaign_with_no_metrics(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics")
    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_empty_analysis_for_a_campaign_with_no_analysis(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/analysis")
    assert response.status_code == 200
    assert response.json() == {"observations": [], "signals": [], "analysis_results": []}


# --- security --------------------------------------------------------------


def test_no_raw_uuid_in_metrics_or_analysis_responses(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    fixtures["client"].post(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics",
        json=_metrics_payload(client_request_id="req-uuid-check"),
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    metrics_text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics").text
    analysis_text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/analysis").text
    assert not _UUID_RE.search(metrics_text)
    assert not _UUID_RE.search(analysis_text)


def test_no_agent_identifiers_or_chain_of_thought_in_responses(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    fixtures["client"].post(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics",
        json=_metrics_payload(client_request_id="req-agent-check"),
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics").text
    text += fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/analysis").text
    assert "AGENT-" not in text
    lowered = text.lower()
    for forbidden in ("chain_of_thought", "reasoning", "agent_id", "api_key", "password"):
        assert forbidden not in lowered


def test_metrics_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.get("/api/v1/campaigns/CMP-FAKE00000000/metrics")
    assert response.status_code == 401


# --- tenancy -----------------------------------------------------------


def test_tenant_a_cannot_read_tenant_bs_metrics(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()

    response = client_a.get(f"/api/v1/campaigns/{body_b['campaign']['id']}/metrics")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_unknown_campaign_id_is_indistinguishable_from_not_yours(campaign_run_client: dict) -> None:
    response = campaign_run_client["client"].get("/api/v1/campaigns/CMP-TOTALLYFAKE0/metrics")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


# --- stage-lifecycle / orchestration non-mutation ---------------------------


def test_posting_metrics_does_not_change_run_status(campaign_run_client: dict, db_session) -> None:
    from app.campaigns.repository import CampaignRunRepository

    fixtures = campaign_run_client
    fixtures["client"].post(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics",
        json=_metrics_payload(client_request_id="req-nonmutation"),
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    from app.campaigns.models import CampaignRunStatus

    run = CampaignRunRepository(db_session).get_by_public_id(fixtures["run_id"])
    assert run.status is CampaignRunStatus.CREATED
