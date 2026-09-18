"""API contract, authorization, tenancy, and non-leakage tests for Governed
Next-Cycle Hypothesis creation (MVP-31B, implementing the frozen
MVP-31A/-31A-R1 contract). All marked `postgres`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as OrmSession

from app.campaigns.repository import CampaignRepository
from app.persistence.session import get_engine
from app.workspaces.models import MembershipRole
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.orchestrationtest import build_base_strategy
from tests.settingstest import add_member_to_workspace, login_as

pytestmark = pytest.mark.postgres


def _hypotheses_path(fixtures: dict, strategy_public_id: str) -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy/{strategy_public_id}/hypotheses"


def _post(fixtures: dict, path: str, json: dict) -> object:
    return fixtures["client"].post(path, json=json, headers={"X-CSRF-Token": fixtures["csrf_token"]})


def _build_base_strategy(campaign_public_id: str) -> str:
    """Bootstraps a real Strategy for an already-HTTP-created campaign via
    the real, production ``StrategyService.record_strategy`` (there is no
    HTTP write route for initial Strategy creation). Returns the
    Strategy's public_id."""
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(campaign_public_id)
        base_strategy, _run, _stage = build_base_strategy(session, campaign=campaign)
        session.commit()
        return base_strategy.public_id


# --- happy path: any active membership (MEMBER+) can create ------------------


def test_owner_can_create_a_hypothesis(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])

    response = _post(fixtures, _hypotheses_path(fixtures, strategy_id), {"statement": "A guarantee increases signups."})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"].startswith("HYP-")
    assert body["statement"] == "A guarantee increases signups."
    assert body["status"] == "OPEN"
    assert "origin" not in body  # MVP-31A-R1: no origin field exposed

    current = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").json()
    assert any(h["id"] == body["id"] for h in current["hypotheses"])


def test_member_can_create_a_hypothesis(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}

    response = _post(member_fixtures, _hypotheses_path(member_fixtures, strategy_id), {"statement": "x"})
    assert response.status_code == 201, response.text


def test_admin_can_create_a_hypothesis(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    admin = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.ADMIN)
    admin_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(admin_client, email=admin["email"], password=admin["password"])
    admin_fixtures = {**fixtures, "client": admin_client, "csrf_token": token}

    response = _post(admin_fixtures, _hypotheses_path(admin_fixtures, strategy_id), {"statement": "x"})
    assert response.status_code == 201, response.text


# --- CSRF ----------------------------------------------------------------------


def test_create_requires_csrf(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    response = fixtures["client"].post(_hypotheses_path(fixtures, strategy_id), json={"statement": "x"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_INVALID"


# --- malformed request ---------------------------------------------------------


def test_create_rejects_empty_statement(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    response = _post(fixtures, _hypotheses_path(fixtures, strategy_id), {"statement": ""})
    assert response.status_code == 422


def test_create_rejects_blank_after_trim_statement(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    response = _post(fixtures, _hypotheses_path(fixtures, strategy_id), {"statement": "   "})
    assert response.status_code == 422


def test_create_trims_the_statement(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    response = _post(fixtures, _hypotheses_path(fixtures, strategy_id), {"statement": "  padded  "})
    assert response.status_code == 201
    assert response.json()["statement"] == "padded"


def test_create_rejects_overlength_statement(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    response = _post(fixtures, _hypotheses_path(fixtures, strategy_id), {"statement": "x" * 4001})
    assert response.status_code == 422


def test_create_rejects_unknown_extra_field(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    response = _post(fixtures, _hypotheses_path(fixtures, strategy_id), {"statement": "x", "status": "CONFIRMED"})
    assert response.status_code == 422


def test_create_rejects_client_supplied_strategy_id_field(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    response = _post(fixtures, _hypotheses_path(fixtures, strategy_id), {"statement": "x", "strategy_id": "STR-FAKE"})
    assert response.status_code == 422


# --- domain gate surfaced over HTTP --------------------------------------------


def test_create_against_an_unknown_strategy_is_a_403(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _post(fixtures, _hypotheses_path(fixtures, "STR-TOTALLYFAKE0"), {"statement": "x"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_create_against_a_stale_strategy_is_a_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    approval_id = _record_approved_approval(fixtures)
    revised = _post(
        fixtures, f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy/{strategy_id}/revision",
        {"strategic_approval_id": approval_id, "summary": "x", "positioning_statement": "y"},
    )
    assert revised.status_code == 201

    stale = _post(fixtures, _hypotheses_path(fixtures, strategy_id), {"statement": "attempted against v1"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "HYPOTHESIS_STRATEGY_STALE"

    # The new current Strategy remains fully eligible.
    ok = _post(fixtures, _hypotheses_path(fixtures, revised.json()["strategy"]["id"]), {"statement": "attempted against v2"})
    assert ok.status_code == 201


# --- no generic mutation surface ------------------------------------------------


def test_no_patch_or_delete_route_exists(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    assert fixtures["client"].patch(_hypotheses_path(fixtures, strategy_id), json={}, headers=headers).status_code == 405
    assert fixtures["client"].delete(_hypotheses_path(fixtures, strategy_id), headers=headers).status_code == 405


# --- tenancy: non-leaky campaign-scoped resolution --------------------------


def test_tenant_a_cannot_create_a_hypothesis_under_tenant_bs_strategy(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()
    campaign_b_id = body_b["campaign"]["id"]
    strategy_b_id = _build_base_strategy(campaign_b_id)

    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_a = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A"), headers={"X-CSRF-Token": csrf_a}).json()
    campaign_a_id = body_a["campaign"]["id"]

    cross_tenant_response = client_a.post(
        f"/api/v1/campaigns/{campaign_a_id}/strategy/{strategy_b_id}/hypotheses",
        json={"statement": "x"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert cross_tenant_response.status_code == 403 and cross_tenant_response.json()["error"]["code"] == "FORBIDDEN"

    # No mutation: B's own current Strategy still has zero Hypotheses.
    still_current = client_b.get(f"/api/v1/campaigns/{campaign_b_id}/strategy").json()
    assert still_current["hypotheses"] == []


# --- shared helper (mirrors tests/test_strategy_revision_api.py) -------------


def _record_approved_approval(fixtures: dict) -> str:
    from tests.test_strategic_decision_api import _build_accepted_recommendation

    recommendation_id = _build_accepted_recommendation(fixtures["campaign_id"])
    decision_response = _post(
        fixtures, f"/api/v1/campaigns/{fixtures['campaign_id']}/strategic-decisions",
        {"strategic_recommendation_candidate_id": recommendation_id, "decision_type": "ADOPT", "statement": "Adopt this direction."},
    )
    assert decision_response.status_code == 201, decision_response.text
    decision_id = decision_response.json()["id"]
    approval_response = _post(
        fixtures, f"/api/v1/campaigns/{fixtures['campaign_id']}/strategic-decisions/{decision_id}/approval",
        {"outcome": "APPROVED"},
    )
    assert approval_response.status_code == 201, approval_response.text
    return approval_response.json()["id"]
