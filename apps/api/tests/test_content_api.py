"""API contract, tenancy, and security tests for the Content read surface
(BACKEND-10 §28/§29/§30). All marked `postgres`.

No public write endpoint exists, so test data is recorded via
``ContentService`` against the live app's own engine (the same pattern
``tests/test_planning_api.py``'s helper uses), then read back through the
real, authenticated HTTP client.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.assets.models import Asset, CreativeBrief
from app.campaigns.repository import CampaignRepository, CampaignRunRepository
from app.content.models import ContentApproval, ContentDistribution, ContentVersion
from app.content.service import ContentService
from app.learning.models import LearningCandidate
from app.measurement.models import MetricEntry
from app.orchestration.models import BusinessStage
from app.orchestration.repository import RunStageExecutionRepository
from app.persistence.session import get_engine
from app.planning.service import PlanningService
from app.tracking.models import TrackingPlan
from app.users.models import User
from app.workspaces.models import Membership, MembershipRole, MembershipStatus
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.contenttest import default_piece_fields, default_version_payload
from tests.orchestrationtest import initialize_run
from tests.planningtest import default_plan_item
from tests.settingstest import add_member_to_workspace, login_as

pytestmark = pytest.mark.postgres

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _record_content_piece(fixtures: dict) -> str:
    """Records one Content Plan + Plan Item + Content Brief + Content
    Piece + initial Content Version for `fixtures`'s campaign/run, via a
    genuinely separate, immediately-committed session bound to the app's
    own engine — so the write is visible to the next HTTP request the
    test client makes. Returns the resulting Content Piece's public id
    (MVP-17B: existing call sites that ignore the return value are
    unaffected)."""
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        run = CampaignRunRepository(session).get_by_public_id(fixtures["run_id"])
        stages = RunStageExecutionRepository(session).list_for_run(campaign_run_id=run.id)
        stages_by_name = {s.stage: s for s in stages}
        plan, items = PlanningService(session).record_plan(
            campaign=campaign, campaign_run=run, stage_execution=stages_by_name[BusinessStage.PLAN],
            summary="Plan.", items=[default_plan_item()],
        )
        content_service = ContentService(session)
        brief = content_service.record_brief(plan_item=items[0], content_plan=plan, brief="Produce a reel.")
        piece, _version = content_service.record_piece(
            content_brief=brief, initial_payload=default_version_payload(), **default_piece_fields()
        )
        return piece.public_id


@pytest.fixture()
def campaign_client_with_stages(campaign_run_client: dict) -> dict:
    initialize_run(campaign_run_client)
    return campaign_run_client


# --- route surface -------------------------------------------------------


def test_only_get_routes_exist_for_content_list(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    path = f"/api/v1/campaigns/{fixtures['campaign_id']}/content"
    assert fixtures["client"].get(path).status_code == 200
    assert fixtures["client"].post(path, json={}).status_code == 405
    assert fixtures["client"].put(path, json={}).status_code == 405
    assert fixtures["client"].patch(path, json={}).status_code == 405
    assert fixtures["client"].delete(path).status_code == 405


def test_only_get_routes_exist_for_content_detail(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_content_piece(fixtures)
    body = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content").json()
    content_id = body["items"][0]["id"]
    path = f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_id}"
    assert fixtures["client"].get(path).status_code == 200
    assert fixtures["client"].post(path, json={}).status_code == 405
    assert fixtures["client"].put(path, json={}).status_code == 405
    assert fixtures["client"].patch(path, json={}).status_code == 405
    assert fixtures["client"].delete(path).status_code == 405


def test_no_content_brief_route_exists(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    response = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content-briefs")
    assert response.status_code == 404


def test_no_bare_approvals_collection_route_exists(campaign_client_with_stages: dict) -> None:
    """MVP-17B added exactly ``.../approvals/{approval_id}/mark-under-review``
    and ``.../approvals/{approval_id}/decision`` — a bare
    ``.../approvals`` collection route (GET list, or POST without an
    approval id segment) was never part of the frozen contract and still
    does not exist."""
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    path = f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_id}/approvals"
    assert fixtures["client"].get(path).status_code == 404
    assert fixtures["client"].post(path, json={}).status_code == 404


# --- response shape --------------------------------------------------------


def test_empty_content_list_for_a_campaign_with_no_content(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    response = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content")
    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_content_list_and_detail_return_expected_shape(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_content_piece(fixtures)

    list_body = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content").json()
    assert len(list_body["items"]) == 1
    item = list_body["items"][0]
    assert item["id"].startswith("CNT-")
    assert item["status"] == "DRAFT"
    assert item["format"] == "Reel"

    detail = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{item['id']}").json()
    assert detail["piece"]["id"] == item["id"]
    assert detail["latest_version"]["id"].startswith("CNV-")
    assert detail["latest_version"]["payload"]["kind"] == "reel"


def test_unknown_content_piece_id_is_indistinguishable_from_not_yours(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    response = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content/CNT-TOTALLYFAKE0")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


# --- security --------------------------------------------------------------


def test_no_raw_uuid_in_content_responses(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_content_piece(fixtures)
    list_text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content").text
    assert not _UUID_RE.search(list_text), "content list response leaked a raw UUID"

    body = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content").json()
    content_id = body["items"][0]["id"]
    detail_text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_id}").text
    assert not _UUID_RE.search(detail_text), "content detail response leaked a raw UUID"


def test_no_agent_identifiers_in_content_responses(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_content_piece(fixtures)
    text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content").text
    assert "AGENT-" not in text
    assert "agent_id" not in text


def test_no_secret_reasoning_or_governance_fields_in_response(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_content_piece(fixtures)
    text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content").text.lower()
    for forbidden in (
        "chain_of_thought", "reasoning", "api_key", "password", "provider",
        "reviewer", "approved_by", "gate_decision", "strategic_decision",
    ):
        assert forbidden not in text


def test_content_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.get("/api/v1/campaigns/CMP-FAKE00000000/content")
    assert response.status_code == 401


# --- tenancy -----------------------------------------------------------


def test_tenant_a_cannot_read_tenant_bs_content(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()

    response = client_a.get(f"/api/v1/campaigns/{body_b['campaign']['id']}/content")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_unknown_campaign_id_is_indistinguishable_from_not_yours(campaign_client_with_stages: dict) -> None:
    response = campaign_client_with_stages["client"].get("/api/v1/campaigns/CMP-TOTALLYFAKE0/content")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_content_piece_belonging_to_another_campaign_is_not_visible(campaign_client_with_stages: dict) -> None:
    fixtures_a = campaign_client_with_stages
    _record_content_piece(fixtures_a)
    body_a = fixtures_a["client"].get(f"/api/v1/campaigns/{fixtures_a['campaign_id']}/content").json()
    content_id = body_a["items"][0]["id"]

    csrf_b = fixtures_a["client"].get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = fixtures_a["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name="Campaign B"), headers={"X-CSRF-Token": csrf_b}
    ).json()
    response = fixtures_a["client"].get(f"/api/v1/campaigns/{body_b['campaign']['id']}/content/{content_id}")
    assert response.status_code == 403


# --- MVP-17B: lifecycle + approval mutation helpers -------------------------


def _lifecycle_path(fixtures: dict, content_id: str, suffix: str) -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_id}{suffix}"


def _post(fixtures: dict, path: str, json: dict | None = None):
    return fixtures["client"].post(path, json=json if json is not None else {}, headers={"X-CSRF-Token": fixtures["csrf_token"]})


def _advance_to_ready_for_review(fixtures: dict, content_id: str) -> dict:
    _post(fixtures, _lifecycle_path(fixtures, content_id, "/mark-in-production"))
    _post(fixtures, _lifecycle_path(fixtures, content_id, "/mark-produced"))
    response = _post(fixtures, _lifecycle_path(fixtures, content_id, "/mark-ready-for-review"))
    assert response.status_code == 200, response.text
    return response.json()


def _request_approval_id(fixtures: dict, content_id: str) -> str:
    response = _post(fixtures, _lifecycle_path(fixtures, content_id, "/request-approval"))
    assert response.status_code == 201, response.text
    return response.json()["latest_approval"]["id"]


def _under_review_approval_id(fixtures: dict, content_id: str) -> str:
    approval_id = _request_approval_id(fixtures, content_id)
    response = _post(fixtures, _lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/mark-under-review"))
    assert response.status_code == 200, response.text
    return approval_id


# --- lifecycle transitions (MVP-17B §15/§59) --------------------------------


def test_lifecycle_transitions_draft_through_ready_for_review(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)

    response = _post(fixtures, _lifecycle_path(fixtures, content_id, "/mark-in-production"))
    assert response.status_code == 200, response.text
    assert response.json()["piece"]["status"] == "IN_PRODUCTION"

    response = _post(fixtures, _lifecycle_path(fixtures, content_id, "/mark-produced"))
    assert response.status_code == 200, response.text
    assert response.json()["piece"]["status"] == "PRODUCED"

    response = _post(fixtures, _lifecycle_path(fixtures, content_id, "/mark-ready-for-review"))
    assert response.status_code == 200, response.text
    assert response.json()["piece"]["status"] == "READY_FOR_REVIEW"


def test_illegal_lifecycle_transition_is_rejected_with_409(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    # DRAFT -> PRODUCED directly is not a legal edge.
    response = _post(fixtures, _lifecycle_path(fixtures, content_id, "/mark-produced"))
    assert response.status_code == 409, response.text


# --- request-approval: precondition + multiplicity (MVP-17A-R1 / MVP-17B §54) ---


def test_request_approval_succeeds_when_ready_for_review_and_no_open_approval(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)

    response = _post(fixtures, _lifecycle_path(fixtures, content_id, "/request-approval"))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["latest_approval"]["status"] == "REQUESTED"
    assert body["latest_approval"]["id"].startswith("APR-")


def test_request_approval_rejected_when_not_ready_for_review(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)  # still DRAFT
    response = _post(fixtures, _lifecycle_path(fixtures, content_id, "/request-approval"))
    assert response.status_code == 409, response.text


def test_request_approval_rejected_when_a_requested_approval_is_already_open(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    first = _post(fixtures, _lifecycle_path(fixtures, content_id, "/request-approval"))
    assert first.status_code == 201, first.text

    second = _post(fixtures, _lifecycle_path(fixtures, content_id, "/request-approval"))
    assert second.status_code == 409, second.text


def test_request_approval_rejected_when_an_under_review_approval_is_already_open(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    _under_review_approval_id(fixtures, content_id)

    response = _post(fixtures, _lifecycle_path(fixtures, content_id, "/request-approval"))
    assert response.status_code == 409, response.text


def test_request_approval_allowed_again_after_rejected_terminal_attempt(campaign_client_with_stages: dict) -> None:
    """REJECTED (unlike CHANGES_REQUESTED — see MVP-20's revision-loop
    tests below) never mutates ContentPiece.status (§27/§28) — the Piece
    remains READY_FOR_REVIEW, so a new approval cycle against the same,
    unrevised Version is legitimately allowed once a REJECTED decision
    has resolved. This deliberate, out-of-scope asymmetry with
    CHANGES_REQUESTED is documented in MVP-20A §J/MVP-20A-R1 §17."""
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    approval_id = _under_review_approval_id(fixtures, content_id)

    decision = _post(
        fixtures, _lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/decision"), {"decision": "REJECTED"}
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["piece"]["status"] == "READY_FOR_REVIEW"

    second = _post(fixtures, _lifecycle_path(fixtures, content_id, "/request-approval"))
    assert second.status_code == 201, second.text
    assert second.json()["latest_approval"]["id"] != approval_id


# --- mark-under-review --------------------------------------------------------


def test_mark_under_review_transitions_requested_to_under_review(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    approval_id = _request_approval_id(fixtures, content_id)

    response = _post(fixtures, _lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/mark-under-review"))
    assert response.status_code == 200, response.text
    assert response.json()["latest_approval"]["status"] == "UNDER_REVIEW"


# --- final decision (MVP-17B §24-§28/§60-§62) -------------------------------


def test_decision_rejected_with_409_when_approval_not_under_review(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    approval_id = _request_approval_id(fixtures, content_id)  # still REQUESTED, not UNDER_REVIEW

    response = _post(fixtures, _lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/decision"), {"decision": "APPROVED"})
    assert response.status_code == 409, response.text


def test_decision_with_disallowed_value_is_rejected(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    approval_id = _under_review_approval_id(fixtures, content_id)

    response = _post(fixtures, _lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/decision"), {"decision": "EXPIRED"})
    assert response.status_code == 422, response.text


def test_decision_changes_requested_moves_piece_to_revision_requested(campaign_client_with_stages: dict) -> None:
    """MVP-20: closes the same-version-reapproval defect — CHANGES_REQUESTED
    now atomically couples the Piece to REVISION_REQUESTED (previously
    left it at READY_FOR_REVIEW; see the revision-loop tests below for
    the full regression proof)."""
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    approval_id = _under_review_approval_id(fixtures, content_id)

    response = _post(
        fixtures, _lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/decision"), {"decision": "CHANGES_REQUESTED"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["latest_approval"]["status"] == "CHANGES_REQUESTED"
    assert body["piece"]["status"] == "REVISION_REQUESTED"


def test_decision_rejected_leaves_piece_status_unchanged(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    approval_id = _under_review_approval_id(fixtures, content_id)

    response = _post(fixtures, _lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/decision"), {"decision": "REJECTED"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["latest_approval"]["status"] == "REJECTED"
    assert body["piece"]["status"] == "READY_FOR_REVIEW"


def test_decision_approved_atomically_advances_piece_and_records_reviewer(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    approval_id = _under_review_approval_id(fixtures, content_id)

    response = _post(fixtures, _lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/decision"), {"decision": "APPROVED"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["latest_approval"]["status"] == "APPROVED"
    assert body["latest_approval"]["decided_at"] is not None
    assert body["piece"]["status"] == "APPROVED"

    with OrmSession(bind=get_engine()) as session:
        approval_row = session.execute(select(ContentApproval).where(ContentApproval.public_id == approval_id)).scalar_one()
        assert approval_row.reviewer_user_id is not None
        assert approval_row.decided_at is not None


# --- revision loop (MVP-20) --------------------------------------------------


def _open_changes_requested_cycle(fixtures: dict, content_id: str) -> str:
    """Drives a fresh, already-READY_FOR_REVIEW content piece through one
    full request->review->CHANGES_REQUESTED cycle via real HTTP calls,
    leaving the Piece at REVISION_REQUESTED. Returns the closed
    approval_id."""
    approval_id = _under_review_approval_id(fixtures, content_id)
    response = _post(
        fixtures, _lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/decision"), {"decision": "CHANGES_REQUESTED"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["piece"]["status"] == "REVISION_REQUESTED"
    return approval_id


def _create_version(fixtures: dict, content_id: str, payload: dict | None = None):
    return _post(
        fixtures, _lifecycle_path(fixtures, content_id, "/versions"),
        {"payload": payload if payload is not None else default_version_payload(hook="Revised hook.")},
    )


def test_create_revision_version_happy_path(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    v1_id = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_id}").json()["latest_version"]["id"]
    _open_changes_requested_cycle(fixtures, content_id)

    response = _create_version(fixtures, content_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["piece"]["status"] == "IN_PRODUCTION"
    assert body["latest_version"]["id"] != v1_id
    assert body["latest_version"]["id"].startswith("CNV-")
    assert body["latest_version"]["payload"]["hook"] == "Revised hook."

    detail = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_id}").json()
    assert detail["latest_version"]["id"] == body["latest_version"]["id"]


def test_create_revision_version_rejected_when_not_revision_requested(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)  # still DRAFT
    response = _create_version(fixtures, content_id)
    assert response.status_code == 409, response.text


def test_create_revision_version_rejected_after_approved(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    approval_id = _under_review_approval_id(fixtures, content_id)
    decision = _post(
        fixtures, _lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/decision"), {"decision": "APPROVED"}
    )
    assert decision.status_code == 200, decision.text

    response = _create_version(fixtures, content_id)
    assert response.status_code == 409, response.text


def test_create_revision_version_rejects_forbidden_extra_fields(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    _open_changes_requested_cycle(fixtures, content_id)

    response = _post(
        fixtures, _lifecycle_path(fixtures, content_id, "/versions"),
        {"payload": default_version_payload(), "created_by_user_id": "USR-SPOOF"},
    )
    assert response.status_code == 422, response.text


# --- the critical MVP-20 regression: no same-version reapproval bypass -----


def test_same_version_reapproval_bypass_is_rejected_end_to_end(campaign_client_with_stages: dict) -> None:
    """The exact defect MVP-20 exists to close: after CHANGES_REQUESTED,
    no normal lifecycle route can move the Piece back to READY_FOR_REVIEW
    without a new ContentVersion, so the same unrevised Version can never
    be resubmitted for approval — proven via real, sequential HTTP calls,
    not merely frontend button visibility."""
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    v1_id = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_id}").json()["latest_version"]["id"]
    _open_changes_requested_cycle(fixtures, content_id)

    bypass = _post(fixtures, _lifecycle_path(fixtures, content_id, "/mark-in-production"))
    assert bypass.status_code == 409, bypass.text

    stale_request = _post(fixtures, _lifecycle_path(fixtures, content_id, "/request-approval"))
    assert stale_request.status_code == 409, stale_request.text

    detail = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_id}").json()
    assert detail["piece"]["status"] == "REVISION_REQUESTED"
    assert detail["latest_version"]["id"] == v1_id

    # The only legitimate way out: create a new version, then resubmit.
    version_response = _create_version(fixtures, content_id)
    assert version_response.status_code == 201, version_response.text
    v2_id = version_response.json()["latest_version"]["id"]
    assert v2_id != v1_id

    _post(fixtures, _lifecycle_path(fixtures, content_id, "/mark-produced"))
    _post(fixtures, _lifecycle_path(fixtures, content_id, "/mark-ready-for-review"))
    resubmit = _post(fixtures, _lifecycle_path(fixtures, content_id, "/request-approval"))
    assert resubmit.status_code == 201, resubmit.text
    assert resubmit.json()["latest_approval"]["status"] == "REQUESTED"

    with OrmSession(bind=get_engine()) as session:
        v2 = session.execute(select(ContentVersion).where(ContentVersion.public_id == v2_id)).scalar_one()
        new_approval = session.execute(
            select(ContentApproval).where(ContentApproval.public_id == resubmit.json()["latest_approval"]["id"])
        ).scalar_one()
        assert new_approval.content_version_id == v2.id, "the new Approval must reference V2, never stale V1"


def test_full_revision_cycle_distribution_freezes_the_approved_v2(campaign_client_with_stages: dict) -> None:
    """Extends the MVP-18/MVP-17 provenance guarantee through a revision:
    once V2 (not V1) is the version that is actually approved,
    ContentDistribution must freeze V2 — no MVP-18 redesign required."""
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    _open_changes_requested_cycle(fixtures, content_id)

    version_response = _create_version(fixtures, content_id)
    assert version_response.status_code == 201, version_response.text
    v2_id = version_response.json()["latest_version"]["id"]

    _post(fixtures, _lifecycle_path(fixtures, content_id, "/mark-produced"))
    _post(fixtures, _lifecycle_path(fixtures, content_id, "/mark-ready-for-review"))
    approval_id = _under_review_approval_id(fixtures, content_id)
    decision = _post(
        fixtures, _lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/decision"), {"decision": "APPROVED"}
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["piece"]["status"] == "APPROVED"

    ready = _post(fixtures, _lifecycle_path(fixtures, content_id, "/distribution/mark-ready-for-distribution"))
    assert ready.status_code == 201, ready.text

    with OrmSession(bind=get_engine()) as session:
        v2 = session.execute(select(ContentVersion).where(ContentVersion.public_id == v2_id)).scalar_one()
        distribution = session.execute(
            select(ContentDistribution).where(ContentDistribution.content_piece_id == v2.content_piece_id)
        ).scalar_one()
        assert distribution.content_version_id == v2.id


# --- versions route: tenancy (MVP-20 §14/§15) --------------------------------


def test_create_version_denied_for_content_piece_in_a_different_campaign(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    _open_changes_requested_cycle(fixtures, content_id)

    csrf_b = fixtures["client"].get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = fixtures["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name="Campaign B"), headers={"X-CSRF-Token": csrf_b}
    ).json()
    other_campaign_id = body_b["campaign"]["id"]

    response = fixtures["client"].post(
        f"/api/v1/campaigns/{other_campaign_id}/content/{content_id}/versions",
        json={"payload": default_version_payload()},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 403, response.text


def test_create_version_denied_for_unknown_campaign(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    response = fixtures["client"].post(
        f"/api/v1/campaigns/CMP-TOTALLYFAKE0/content/{content_id}/versions",
        json={"payload": default_version_payload()},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 403, response.text


def test_create_version_denied_across_tenants(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")
    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    campaign_a = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A"), headers={"X-CSRF-Token": csrf_a}).json()

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]

    response = client_b.post(
        f"/api/v1/campaigns/{campaign_a['campaign']['id']}/content/CNT-FAKE0000000/versions",
        json={"payload": default_version_payload()},
        headers={"X-CSRF-Token": csrf_b},
    )
    assert response.status_code == 403, response.text


def test_create_version_denied_for_content_piece_in_same_workspace_different_campaign(
    campaign_client_with_stages: dict,
) -> None:
    """Same workspace, correct owner, but the Piece belongs to a
    different Campaign than the one named in the URL — the composite
    Piece<->Campaign scoping must still reject it non-leakily."""
    fixtures = campaign_client_with_stages
    content_id_a = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id_a)
    _open_changes_requested_cycle(fixtures, content_id_a)

    csrf_b = fixtures["client"].get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = fixtures["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name="Campaign B (same workspace)"), headers={"X-CSRF-Token": csrf_b}
    ).json()
    campaign_b_id = body_b["campaign"]["id"]

    response = fixtures["client"].post(
        f"/api/v1/campaigns/{campaign_b_id}/content/{content_id_a}/versions",
        json={"payload": default_version_payload()},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 403, response.text


# --- versions route: authorization + CSRF ------------------------------------


def test_create_revision_version_allowed_for_member_and_admin(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]

    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    admin = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.ADMIN)

    for credentials in (member, admin):
        content_id = _record_content_piece(fixtures)
        _advance_to_ready_for_review(fixtures, content_id)
        _open_changes_requested_cycle(fixtures, content_id)

        client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
        csrf = login_as(client, email=credentials["email"], password=credentials["password"])
        response = client.post(
            f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_id}/versions",
            json={"payload": default_version_payload()},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 201, response.text


def test_create_revision_version_requires_csrf(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    _open_changes_requested_cycle(fixtures, content_id)

    response = fixtures["client"].post(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_id}/versions",
        json={"payload": default_version_payload()},
    )
    assert response.status_code == 403, response.text


# --- tenancy: mutation routes (MVP-17B §14/§56) -----------------------------


def test_mutation_denied_for_content_piece_in_a_different_campaign(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)

    csrf_b = fixtures["client"].get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = fixtures["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name="Campaign B"), headers={"X-CSRF-Token": csrf_b}
    ).json()
    other_campaign_id = body_b["campaign"]["id"]

    response = fixtures["client"].post(
        f"/api/v1/campaigns/{other_campaign_id}/content/{content_id}/mark-in-production",
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 403, response.text


def test_mutation_denied_for_unknown_campaign(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    response = fixtures["client"].post(
        f"/api/v1/campaigns/CMP-TOTALLYFAKE0/content/{content_id}/mark-in-production",
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 403, response.text


def test_mutation_denied_across_tenants(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")
    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    campaign_a = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A"), headers={"X-CSRF-Token": csrf_a}).json()

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]

    response = client_b.post(
        f"/api/v1/campaigns/{campaign_a['campaign']['id']}/content/CNT-FAKE0000000/mark-in-production",
        headers={"X-CSRF-Token": csrf_b},
    )
    assert response.status_code == 403, response.text


def test_approval_action_denied_when_approval_belongs_to_a_different_content_piece(campaign_client_with_stages: dict) -> None:
    """MVP-17B §14/§56 — the critical URL-substitution guard: an Approval
    opened for Content Piece A must not be actionable through Content
    Piece B's URL, even within the same workspace and campaign."""
    fixtures = campaign_client_with_stages
    content_id_a = _record_content_piece(fixtures)
    content_id_b = _record_content_piece(fixtures)

    _advance_to_ready_for_review(fixtures, content_id_a)
    approval_id = _request_approval_id(fixtures, content_id_a)

    # Piece B must independently reach READY_FOR_REVIEW to isolate the
    # test to the approval/piece mismatch itself, not an unrelated
    # lifecycle-state 409.
    _advance_to_ready_for_review(fixtures, content_id_b)

    response = _post(fixtures, _lifecycle_path(fixtures, content_id_b, f"/approvals/{approval_id}/mark-under-review"))
    assert response.status_code == 403, response.text

    decision_response = _post(
        fixtures, _lifecycle_path(fixtures, content_id_b, f"/approvals/{approval_id}/decision"), {"decision": "APPROVED"}
    )
    assert decision_response.status_code == 403, decision_response.text


# --- authorization: routine vs. final-decision routes (MVP-17B §11/§57) -----


def test_routine_actions_allowed_for_member_and_admin(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]

    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    admin = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.ADMIN)

    for credentials in (member, admin):
        content_id = _record_content_piece(fixtures)
        client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
        csrf = login_as(client, email=credentials["email"], password=credentials["password"])
        response = client.post(
            f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_id}/mark-in-production",
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200, response.text


def test_decision_route_forbidden_for_member(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    approval_id = _under_review_approval_id(fixtures, content_id)

    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    member_csrf = login_as(member_client, email=member["email"], password=member["password"])

    response = member_client.post(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_id}/approvals/{approval_id}/decision",
        json={"decision": "APPROVED"},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert response.status_code == 403, response.text


def test_decision_route_allowed_for_admin(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    approval_id = _under_review_approval_id(fixtures, content_id)

    admin = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.ADMIN)
    admin_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    admin_csrf = login_as(admin_client, email=admin["email"], password=admin["password"])

    response = admin_client.post(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_id}/approvals/{approval_id}/decision",
        json={"decision": "APPROVED"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert response.status_code == 200, response.text


def test_decision_route_allowed_for_owner(campaign_client_with_stages: dict) -> None:
    """The default fixture user is already OWNER of their own,
    freshly-created workspace (``app/auth/service.py``'s own
    registration behavior) — no extra setup required."""
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    approval_id = _under_review_approval_id(fixtures, content_id)

    response = _post(fixtures, _lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/decision"), {"decision": "APPROVED"})
    assert response.status_code == 200, response.text


def test_content_mutation_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.post("/api/v1/campaigns/CMP-FAKE00000000/content/CNT-FAKE0000000/mark-in-production")
    assert response.status_code == 401, response.text


def test_content_mutation_denied_for_revoked_membership(campaign_client_with_stages: dict) -> None:
    """No public "revoke membership" endpoint exists in this codebase —
    revocation is simulated via direct persistence, mirroring
    ``tests/settingstest.py::add_member_to_workspace``'s own "no invite
    endpoint exists" precedent."""
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    me = fixtures["client"].get("/api/v1/users/me").json()

    with OrmSession(bind=get_engine()) as session:
        user = session.execute(select(User).where(User.public_id == me["id"])).scalar_one()
        membership = session.execute(select(Membership).where(Membership.user_id == user.id)).scalar_one()
        membership.status = MembershipStatus.REVOKED
        session.commit()

    response = fixtures["client"].post(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_id}/mark-in-production",
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 403, response.text


# --- CSRF (MVP-17B §33/§58) --------------------------------------------------


@pytest.mark.parametrize(
    "suffix", ["/mark-in-production", "/mark-produced", "/mark-ready-for-review", "/request-approval"]
)
def test_lifecycle_and_request_approval_routes_require_csrf(campaign_client_with_stages: dict, suffix: str) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    response = fixtures["client"].post(_lifecycle_path(fixtures, content_id, suffix))
    assert response.status_code == 403, response.text


def test_mark_under_review_requires_csrf(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    approval_id = _request_approval_id(fixtures, content_id)

    response = fixtures["client"].post(_lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/mark-under-review"))
    assert response.status_code == 403, response.text


def test_decision_requires_csrf(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    approval_id = _under_review_approval_id(fixtures, content_id)

    response = fixtures["client"].post(
        _lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/decision"), json={"decision": "APPROVED"}
    )
    assert response.status_code == 403, response.text


# --- governance side-effect boundary (MVP-17B §64) --------------------------


def test_approved_decision_creates_no_asset_tracking_metric_or_learning_rows(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    approval_id = _under_review_approval_id(fixtures, content_id)

    response = _post(fixtures, _lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/decision"), {"decision": "APPROVED"})
    assert response.status_code == 200, response.text

    with OrmSession(bind=get_engine()) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        piece = ContentService(session).pieces.get_by_public_id(content_id)

        asset_count = session.execute(
            select(func.count())
            .select_from(Asset)
            .join(CreativeBrief, Asset.creative_brief_id == CreativeBrief.id)
            .where(CreativeBrief.content_piece_id == piece.id)
        ).scalar_one()
        assert asset_count == 0, "[GOVERNANCE] approval must never create an Asset"

        tracking_count = session.execute(
            select(func.count()).select_from(TrackingPlan).where(TrackingPlan.campaign_id == campaign.id)
        ).scalar_one()
        assert tracking_count == 0, "[GOVERNANCE] approval must never create a TrackingPlan"

        metric_count = session.execute(
            select(func.count()).select_from(MetricEntry).where(MetricEntry.campaign_id == campaign.id)
        ).scalar_one()
        assert metric_count == 0, "[GOVERNANCE] approval must never create a MetricEntry"

        learning_count = session.execute(
            select(func.count()).select_from(LearningCandidate).where(LearningCandidate.workspace_id == campaign.workspace_id)
        ).scalar_one()
        assert learning_count == 0, "[GOVERNANCE] approval must never create a LearningCandidate"
