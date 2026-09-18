"""API contract, authorization, tenancy, and non-leakage tests for
Governed Strategy Revision (MVP-30B, implementing the frozen
MVP-30A/-30A-R1 contract). All marked `postgres`.

Reuses ``tests/test_strategic_decision_api.py``/``test_strategic_approval_api.py``'s
own ancestry-building helpers (a governed Revision always needs a real,
currently-ADOPT StrategicDecision with an APPROVED, unconsumed
StrategicApproval, plus a real bootstrap-origin base Strategy)."""

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
from tests.test_strategic_decision_api import _build_accepted_recommendation

pytestmark = pytest.mark.postgres


def _decisions_path(fixtures: dict, suffix: str = "") -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/strategic-decisions{suffix}"


def _revision_path(fixtures: dict, base_strategy_public_id: str) -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy/{base_strategy_public_id}/revision"


def _history_path(fixtures: dict) -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy/history"


def _post(fixtures: dict, path: str, json: dict) -> object:
    return fixtures["client"].post(path, json=json, headers={"X-CSRF-Token": fixtures["csrf_token"]})


def _record_adopt_decision(fixtures: dict) -> str:
    recommendation_id = _build_accepted_recommendation(fixtures["campaign_id"])
    response = _post(
        fixtures, _decisions_path(fixtures),
        {"strategic_recommendation_candidate_id": recommendation_id, "decision_type": "ADOPT", "statement": "Adopt this direction."},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _record_approved_approval(fixtures: dict) -> str:
    """Builds a full, real, currently-ADOPT StrategicDecision with an
    APPROVED, unconsumed StrategicApproval via HTTP. Returns the
    Approval's public_id — the sole implemented Revision-eligible input."""
    decision_id = _record_adopt_decision(fixtures)
    response = _post(
        fixtures, _decisions_path(fixtures, f"/{decision_id}/approval"), {"outcome": "APPROVED"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _build_base_strategy(campaign_public_id: str) -> str:
    """Bootstraps a real Strategy for an already-HTTP-created campaign via
    the real, production ``StrategyService.record_strategy`` (there is no
    HTTP write route for initial Strategy creation — BACKEND-08 §19).
    Returns the Strategy's public_id."""
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(campaign_public_id)
        base_strategy, _run, _stage = build_base_strategy(session, campaign=campaign)
        session.commit()
        return base_strategy.public_id


# --- happy path: OWNER can revise --------------------------------------------


def test_owner_can_revise_a_strategy(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    base_strategy_id = _build_base_strategy(fixtures["campaign_id"])
    approval_id = _record_approved_approval(fixtures)

    response = _post(
        fixtures, _revision_path(fixtures, base_strategy_id),
        {"strategic_approval_id": approval_id, "summary": "Revised strategy.", "positioning_statement": "Revised positioning."},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["strategy"]["origin"] == "REVISION"
    assert body["strategy"]["version"] == 2
    assert body["strategy"]["summary"] == "Revised strategy."
    assert body["positioning"]["statement"] == "Revised positioning."
    assert body["revision"]["strategic_approval_id"] == approval_id
    assert body["revision"]["base_strategy_id"] == base_strategy_id
    assert body["revision"]["result_strategy_id"] == body["strategy"]["id"]

    # Current Strategy reflects the revision.
    current = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").json()
    assert current["strategy"]["id"] == body["strategy"]["id"]
    assert current["positioning"]["statement"] == "Revised positioning."


def test_admin_can_revise_a_strategy(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    base_strategy_id = _build_base_strategy(fixtures["campaign_id"])
    approval_id = _record_approved_approval(fixtures)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    admin = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.ADMIN)
    admin_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(admin_client, email=admin["email"], password=admin["password"])
    admin_fixtures = {**fixtures, "client": admin_client, "csrf_token": token}

    response = _post(
        admin_fixtures, _revision_path(admin_fixtures, base_strategy_id),
        {"strategic_approval_id": approval_id, "summary": "x", "positioning_statement": "y"},
    )
    assert response.status_code == 201


# --- history read surface -----------------------------------------------------


def test_history_shows_bootstrap_then_revision_provenance(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    base_strategy_id = _build_base_strategy(fixtures["campaign_id"])
    approval_id = _record_approved_approval(fixtures)
    revised = _post(
        fixtures, _revision_path(fixtures, base_strategy_id),
        {"strategic_approval_id": approval_id, "summary": "x", "positioning_statement": "y"},
    ).json()

    history = fixtures["client"].get(_history_path(fixtures)).json()
    items = {item["strategy"]["id"]: item for item in history["items"]}
    assert items[base_strategy_id]["strategy"]["origin"] == "BOOTSTRAP"
    assert items[base_strategy_id]["revision"] is None  # never fabricated

    result_id = revised["strategy"]["id"]
    assert items[result_id]["strategy"]["origin"] == "REVISION"
    assert items[result_id]["revision"]["id"] == revised["revision"]["id"]
    assert items[result_id]["revision"]["base_strategy_id"] == base_strategy_id


# --- application authority: MEMBER cannot write -------------------------------


def test_member_cannot_revise_a_strategy(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    base_strategy_id = _build_base_strategy(fixtures["campaign_id"])
    approval_id = _record_approved_approval(fixtures)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}

    response = _post(
        member_fixtures, _revision_path(member_fixtures, base_strategy_id),
        {"strategic_approval_id": approval_id, "summary": "x", "positioning_statement": "y"},
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"

    # Member can still read.
    current = member_client.get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").json()
    assert current["strategy"]["id"] == base_strategy_id
    history = member_client.get(_history_path(fixtures)).json()
    assert len(history["items"]) == 1

    # No mutation occurred: OWNER can still revise afterward.
    owner_response = _post(
        fixtures, _revision_path(fixtures, base_strategy_id),
        {"strategic_approval_id": approval_id, "summary": "x", "positioning_statement": "y"},
    )
    assert owner_response.status_code == 201


# --- CSRF ----------------------------------------------------------------------


def test_revise_requires_csrf(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    base_strategy_id = _build_base_strategy(fixtures["campaign_id"])
    approval_id = _record_approved_approval(fixtures)
    response = fixtures["client"].post(
        _revision_path(fixtures, base_strategy_id),
        json={"strategic_approval_id": approval_id, "summary": "x", "positioning_statement": "y"},
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_INVALID"


# --- malformed request ---------------------------------------------------------


def test_revise_rejects_empty_summary(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    base_strategy_id = _build_base_strategy(fixtures["campaign_id"])
    approval_id = _record_approved_approval(fixtures)
    response = _post(
        fixtures, _revision_path(fixtures, base_strategy_id),
        {"strategic_approval_id": approval_id, "summary": "", "positioning_statement": "y"},
    )
    assert response.status_code == 422


def test_revise_rejects_unknown_extra_field(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    base_strategy_id = _build_base_strategy(fixtures["campaign_id"])
    approval_id = _record_approved_approval(fixtures)
    response = _post(
        fixtures, _revision_path(fixtures, base_strategy_id),
        {"strategic_approval_id": approval_id, "summary": "x", "positioning_statement": "y", "version": 99},
    )
    assert response.status_code == 422


# --- domain gate surfaced over HTTP --------------------------------------------


def test_revise_with_a_defer_decisions_nonexistent_approval_is_a_403(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    base_strategy_id = _build_base_strategy(fixtures["campaign_id"])
    response = _post(
        fixtures, _revision_path(fixtures, base_strategy_id),
        {"strategic_approval_id": "SAP-TOTALLYFAKE0", "summary": "x", "positioning_statement": "y"},
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_second_revision_attempt_with_the_same_approval_is_a_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    base_strategy_id = _build_base_strategy(fixtures["campaign_id"])
    approval_id = _record_approved_approval(fixtures)
    first = _post(
        fixtures, _revision_path(fixtures, base_strategy_id),
        {"strategic_approval_id": approval_id, "summary": "x", "positioning_statement": "y"},
    )
    assert first.status_code == 201
    # Targets the NEW current Strategy (the first revision's own result) —
    # still fails, because the constraint is on the Approval, not the base.
    second = _post(
        fixtures, _revision_path(fixtures, first.json()["strategy"]["id"]),
        {"strategic_approval_id": approval_id, "summary": "x2", "positioning_statement": "y2"},
    )
    assert second.status_code == 409 and second.json()["error"]["code"] == "STRATEGIC_APPROVAL_ALREADY_CONSUMED"


def test_revise_a_stale_base_is_a_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    base_strategy_id = _build_base_strategy(fixtures["campaign_id"])
    first_approval_id = _record_approved_approval(fixtures)
    first = _post(
        fixtures, _revision_path(fixtures, base_strategy_id),
        {"strategic_approval_id": first_approval_id, "summary": "x", "positioning_statement": "y"},
    )
    assert first.status_code == 201

    second_approval_id = _record_approved_approval(fixtures)
    stale = _post(
        fixtures, _revision_path(fixtures, base_strategy_id),  # base_strategy_id is now historical
        {"strategic_approval_id": second_approval_id, "summary": "x2", "positioning_statement": "y2"},
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "STRATEGY_REVISION_BASE_STALE"


# --- no generic mutation surface ----------------------------------------------


def test_no_patch_or_delete_route_exists(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    base_strategy_id = _build_base_strategy(fixtures["campaign_id"])
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    assert fixtures["client"].patch(_revision_path(fixtures, base_strategy_id), json={}, headers=headers).status_code == 405
    assert fixtures["client"].delete(_revision_path(fixtures, base_strategy_id), headers=headers).status_code == 405


# --- tenancy: non-leaky campaign-scoped resolution --------------------------


def test_unknown_base_strategy_is_forbidden_non_leakily(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    approval_id = _record_approved_approval(fixtures)
    response = _post(
        fixtures, _revision_path(fixtures, "STR-TOTALLYFAKE0"),
        {"strategic_approval_id": approval_id, "summary": "x", "positioning_statement": "y"},
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_tenant_a_cannot_revise_tenant_bs_strategy(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()
    campaign_b_id = body_b["campaign"]["id"]
    base_strategy_b_id = _build_base_strategy(campaign_b_id)
    fixtures_b = {"client": client_b, "csrf_token": csrf_b, "campaign_id": campaign_b_id}
    approval_b_id = _record_approved_approval(fixtures_b)

    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_a = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A"), headers={"X-CSRF-Token": csrf_a}).json()
    campaign_a_id = body_a["campaign"]["id"]

    cross_tenant_response = client_a.post(
        f"/api/v1/campaigns/{campaign_a_id}/strategy/{base_strategy_b_id}/revision",
        json={"strategic_approval_id": approval_b_id, "summary": "x", "positioning_statement": "y"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert cross_tenant_response.status_code == 403 and cross_tenant_response.json()["error"]["code"] == "FORBIDDEN"

    # No mutation: B's own Strategy is still current and revisable normally.
    still_current = client_b.get(f"/api/v1/campaigns/{campaign_b_id}/strategy").json()
    assert still_current["strategy"]["id"] == base_strategy_b_id
