"""API contract, tenancy, and security tests for the Assets read surface
(BACKEND-13 §15). All marked `postgres`.

No public write endpoint exists, so test data is recorded via
``AssetsService``/``ContentService`` against the live app's own engine
(the same pattern ``tests/test_content_api.py``'s helper uses), then read
back through the real, authenticated HTTP client.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as OrmSession

from app.assets.service import AssetsService
from app.campaigns.repository import CampaignRepository, CampaignRunRepository
from app.content.service import ContentService
from app.orchestration.models import BusinessStage
from app.orchestration.repository import RunStageExecutionRepository
from app.persistence.session import get_engine
from app.planning.service import PlanningService
from tests.assetstest import default_asset_fields, default_creative_brief_spec
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.contenttest import default_piece_fields, default_version_payload
from tests.orchestrationtest import initialize_run
from tests.planningtest import default_plan_item

pytestmark = pytest.mark.postgres

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _record_content_piece(fixtures: dict) -> str:
    """Records one Content Plan + Plan Item + Content Brief + Content
    Piece + initial Content Version for `fixtures`'s campaign/run, via a
    genuinely separate, immediately-committed session — returns the
    resulting Content Piece's public id."""
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


def _record_asset(content_public_id: str, **overrides: object) -> None:
    """Records one CreativeBrief + Asset + initial AssetVersion for the
    given Content Piece, via a genuinely separate, immediately-committed
    session bound to the app's own engine."""
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        piece = ContentService(session).pieces.get_by_public_id(content_public_id)
        assets_service = AssetsService(session)
        creative_brief = assets_service.record_creative_brief(content_piece=piece, spec=default_creative_brief_spec())
        fields = default_asset_fields()
        fields.update(overrides)
        assets_service.record_asset(creative_brief=creative_brief, **fields)


@pytest.fixture()
def campaign_client_with_stages(campaign_run_client: dict) -> dict:
    initialize_run(campaign_run_client)
    return campaign_run_client


def _assets_path(fixtures: dict, content_public_id: str) -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_public_id}/assets"


# --- route surface -------------------------------------------------------


def test_only_get_post_routes_exist_put_patch_delete_rejected(campaign_client_with_stages: dict) -> None:
    """MVP-16B: POST now legitimately creates a Creative Brief/Asset/
    AssetVersion (201) instead of the old blanket 405 — PUT/PATCH/DELETE
    remain unsupported on every Assets route."""
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    path = _assets_path(fixtures, content_public_id)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    assert fixtures["client"].get(path).status_code == 200
    assert fixtures["client"].put(path, json={}).status_code == 405
    assert fixtures["client"].patch(path, json={}).status_code == 405
    assert fixtures["client"].delete(path).status_code == 405
    assert fixtures["client"].put(f"{path}/creative-brief", json={"spec": {}}).status_code == 405
    assert fixtures["client"].delete(f"{path}/creative-brief").status_code == 405
    # Consume the POST route itself so this test does not leave a stray
    # Creative Brief behind for tests sharing the fixture's campaign — a
    # duplicate second creation is what actually proves 201 happened.
    created = fixtures["client"].post(f"{path}/creative-brief", json={"spec": {}}, headers=headers)
    assert created.status_code == 201, created.text
    assert fixtures["client"].post(f"{path}/creative-brief", json={"spec": {}}, headers=headers).status_code == 409


# --- response shape --------------------------------------------------------


def test_empty_response_for_content_piece_with_no_creative_brief(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    response = fixtures["client"].get(_assets_path(fixtures, content_public_id))
    assert response.status_code == 200
    assert response.json() == {"creative_brief": None, "assets": []}


def test_creative_brief_spec_appears_when_a_creative_brief_exists(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    _record_asset(content_public_id)

    body = fixtures["client"].get(_assets_path(fixtures, content_public_id)).json()
    assert body["creative_brief"] == {"spec": default_creative_brief_spec()}


def test_creative_brief_is_null_when_none_exists(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    body = fixtures["client"].get(_assets_path(fixtures, content_public_id)).json()
    assert body["creative_brief"] is None
    assert body["assets"] == []


def test_no_creative_brief_uuid_or_forbidden_fields_leak(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    _record_asset(content_public_id)

    body = fixtures["client"].get(_assets_path(fixtures, content_public_id)).json()
    creative_brief_keys = set(body["creative_brief"].keys())
    assert creative_brief_keys == {"spec"}
    text = fixtures["client"].get(_assets_path(fixtures, content_public_id)).text
    assert not _UUID_RE.search(text), "creative_brief response leaked a raw UUID"


def test_asset_list_returns_expected_shape_with_current_version(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    _record_asset(content_public_id, kind="image", metadata={"width": 1080})

    body = fixtures["client"].get(_assets_path(fixtures, content_public_id)).json()
    assert len(body["assets"]) == 1
    item = body["assets"][0]
    assert item["id"].startswith("AST-")
    assert item["kind"] == "image"
    assert item["status"] is None
    assert item["current_version"]["metadata"] == {"width": 1080}
    assert "created_at" in item["current_version"]
    # No AssetVersion internal id is ever exposed — only its safe fields.
    assert set(item["current_version"].keys()) == {"storage_reference", "metadata", "created_at"}


def test_archived_asset_excluded_from_api_response(campaign_client_with_stages: dict) -> None:
    from sqlalchemy.orm import Session as OrmSession2

    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    _record_asset(content_public_id)

    engine = get_engine()
    with OrmSession2(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        piece = ContentService(session).pieces.get_by_public_id(content_public_id)
        asset = AssetsService(session).list_active_assets_for_content_piece(content_piece_id=piece.id)[0]
        AssetsService(session).archive_asset(workspace_id=campaign.workspace_id, asset_public_id=asset.public_id)

    body = fixtures["client"].get(_assets_path(fixtures, content_public_id)).json()
    # The CreativeBrief itself is untouched by archiving its only Asset —
    # only the Asset is excluded.
    assert body["creative_brief"] == {"spec": default_creative_brief_spec()}
    assert body["assets"] == []


def test_unknown_content_piece_id_is_indistinguishable_from_not_yours(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    response = fixtures["client"].get(_assets_path(fixtures, "CNT-TOTALLYFAKE0"))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


# --- security --------------------------------------------------------------


def test_no_raw_uuid_in_assets_response(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    _record_asset(content_public_id)
    text = fixtures["client"].get(_assets_path(fixtures, content_public_id)).text
    assert not _UUID_RE.search(text), "assets list response leaked a raw UUID"


def test_no_secret_or_governance_fields_in_assets_response(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    _record_asset(content_public_id)
    text = fixtures["client"].get(_assets_path(fixtures, content_public_id)).text.lower()
    for forbidden in ("chain_of_thought", "reasoning", "api_key", "password", "agent_id", "workspace_id"):
        assert forbidden not in text


def test_assets_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.get("/api/v1/campaigns/CMP-FAKE00000000/content/CNT-FAKE00000000/assets")
    assert response.status_code == 401


# --- tenancy -----------------------------------------------------------


def test_tenant_a_cannot_read_tenant_bs_assets(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()

    response = client_a.get(f"/api/v1/campaigns/{body_b['campaign']['id']}/content/CNT-FAKE00000000/assets")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_content_piece_belonging_to_another_campaign_is_not_visible(campaign_client_with_stages: dict) -> None:
    fixtures_a = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures_a)
    _record_asset(content_public_id)

    csrf_b = fixtures_a["client"].get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = fixtures_a["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name="Campaign B"), headers={"X-CSRF-Token": csrf_b}
    ).json()
    response = fixtures_a["client"].get(f"/api/v1/campaigns/{body_b['campaign']['id']}/content/{content_public_id}/assets")
    assert response.status_code == 403


# --- CREATE CreativeBrief (MVP-16B) ----------------------------------------


def test_create_creative_brief_happy_path(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    response = fixtures["client"].post(
        f"{_assets_path(fixtures, content_public_id)}/creative-brief",
        json={"spec": {"tone": "playful"}},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 201, response.text
    assert response.json()["creative_brief"] == {"spec": {"tone": "playful"}}
    assert response.json()["assets"] == []


def test_create_creative_brief_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/campaigns/CMP-FAKE00000000/content/CNT-FAKE00000000/assets/creative-brief", json={"spec": {}}
    )
    assert response.status_code == 401


def test_create_creative_brief_requires_csrf(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    response = fixtures["client"].post(
        f"{_assets_path(fixtures, content_public_id)}/creative-brief", json={"spec": {}}
    )
    assert response.status_code == 403


def test_create_creative_brief_cross_tenant_rejected_non_leakily(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()

    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    response = client_a.post(
        f"/api/v1/campaigns/{body_b['campaign']['id']}/content/CNT-FAKE00000000/assets/creative-brief",
        json={"spec": {}},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_create_creative_brief_duplicate_is_conflict(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    path = f"{_assets_path(fixtures, content_public_id)}/creative-brief"
    first = fixtures["client"].post(path, json={"spec": {}}, headers=headers)
    assert first.status_code == 201
    second = fixtures["client"].post(path, json={"spec": {}}, headers=headers)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CREATIVE_BRIEF_ALREADY_EXISTS"


def test_create_creative_brief_non_object_spec_rejected(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    response = fixtures["client"].post(
        f"{_assets_path(fixtures, content_public_id)}/creative-brief",
        json={"spec": "not an object"},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 422


# --- CREATE Asset (MVP-16B) --------------------------------------------------


def test_create_asset_happy_path(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    path = _assets_path(fixtures, content_public_id)
    fixtures["client"].post(f"{path}/creative-brief", json={"spec": {}}, headers=headers)

    response = fixtures["client"].post(path, json={"kind": "image", "storage_reference": "https://example.com/a.png"}, headers=headers)
    assert response.status_code == 201, response.text
    assets = response.json()["assets"]
    assert len(assets) == 1
    assert assets[0]["kind"] == "image"
    assert assets[0]["status"] is None
    assert assets[0]["id"].startswith("AST-")
    assert assets[0]["current_version"]["storage_reference"] == "https://example.com/a.png"


def test_create_asset_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/campaigns/CMP-FAKE00000000/content/CNT-FAKE00000000/assets", json={"kind": "image"}
    )
    assert response.status_code == 401


def test_create_asset_requires_csrf(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    path = _assets_path(fixtures, content_public_id)
    fixtures["client"].post(f"{path}/creative-brief", json={"spec": {}}, headers={"X-CSRF-Token": fixtures["csrf_token"]})
    response = fixtures["client"].post(path, json={"kind": "image"})
    assert response.status_code == 403


def test_create_asset_with_no_creative_brief_is_forbidden(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    response = fixtures["client"].post(
        _assets_path(fixtures, content_public_id), json={"kind": "image"}, headers={"X-CSRF-Token": fixtures["csrf_token"]}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_create_multiple_assets_under_one_creative_brief(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    path = _assets_path(fixtures, content_public_id)
    fixtures["client"].post(f"{path}/creative-brief", json={"spec": {}}, headers=headers)

    first = fixtures["client"].post(path, json={"kind": "image"}, headers=headers)
    second = fixtures["client"].post(path, json={"kind": "video"}, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assets = second.json()["assets"]
    assert len(assets) == 2
    assert assets[0]["id"] != assets[1]["id"]


def test_create_asset_empty_kind_rejected(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    path = _assets_path(fixtures, content_public_id)
    fixtures["client"].post(f"{path}/creative-brief", json={"spec": {}}, headers=headers)
    response = fixtures["client"].post(path, json={"kind": ""}, headers=headers)
    assert response.status_code == 422


def test_create_asset_extra_field_rejected(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    path = _assets_path(fixtures, content_public_id)
    fixtures["client"].post(f"{path}/creative-brief", json={"spec": {}}, headers=headers)
    response = fixtures["client"].post(path, json={"kind": "image", "status": "approved"}, headers=headers)
    assert response.status_code == 422


# --- APPEND AssetVersion (MVP-16B) ------------------------------------------


def test_append_asset_version_happy_path(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    path = _assets_path(fixtures, content_public_id)
    fixtures["client"].post(f"{path}/creative-brief", json={"spec": {}}, headers=headers)
    created = fixtures["client"].post(path, json={"kind": "image"}, headers=headers)
    asset_id = created.json()["assets"][0]["id"]

    response = fixtures["client"].post(
        f"{path}/{asset_id}/versions", json={"storage_reference": "https://example.com/v2.png"}, headers=headers
    )
    assert response.status_code == 201, response.text
    assert response.json()["assets"][0]["current_version"]["storage_reference"] == "https://example.com/v2.png"


def test_append_asset_version_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/campaigns/CMP-FAKE00000000/content/CNT-FAKE00000000/assets/AST-FAKE00000000/versions", json={}
    )
    assert response.status_code == 401


def test_append_asset_version_requires_csrf(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    path = _assets_path(fixtures, content_public_id)
    fixtures["client"].post(f"{path}/creative-brief", json={"spec": {}}, headers=headers)
    created = fixtures["client"].post(path, json={"kind": "image"}, headers=headers)
    asset_id = created.json()["assets"][0]["id"]

    response = fixtures["client"].post(f"{path}/{asset_id}/versions", json={})
    assert response.status_code == 403


def test_append_asset_version_on_archived_asset_is_conflict(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    path = _assets_path(fixtures, content_public_id)
    fixtures["client"].post(f"{path}/creative-brief", json={"spec": {}}, headers=headers)
    created = fixtures["client"].post(path, json={"kind": "image"}, headers=headers)
    asset_id = created.json()["assets"][0]["id"]

    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        AssetsService(session).archive_asset(workspace_id=campaign.workspace_id, asset_public_id=asset_id)

    response = fixtures["client"].post(f"{path}/{asset_id}/versions", json={}, headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ASSET_ARCHIVED"


def test_append_asset_version_for_asset_from_another_content_piece_is_forbidden(campaign_client_with_stages: dict) -> None:
    """MVP-16A §AD: an Asset belonging to a different Content Piece's own
    Creative Brief, even within the same workspace, must be rejected via
    this Content Piece's URL — the underlying service only re-verifies
    workspace-level tenancy, so the router itself must close this gap."""
    fixtures = campaign_client_with_stages
    content_public_id_a = _record_content_piece(fixtures)
    content_public_id_b = _record_content_piece(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    path_a = _assets_path(fixtures, content_public_id_a)
    path_b = _assets_path(fixtures, content_public_id_b)

    fixtures["client"].post(f"{path_b}/creative-brief", json={"spec": {}}, headers=headers)
    created_b = fixtures["client"].post(path_b, json={"kind": "image"}, headers=headers)
    asset_b_id = created_b.json()["assets"][0]["id"]

    response = fixtures["client"].post(f"{path_a}/{asset_b_id}/versions", json={}, headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


# --- full manual flow --------------------------------------------------------


def test_full_manual_flow_create_brief_asset_and_append_version(campaign_client_with_stages: dict) -> None:
    """Proves the whole MVP-16B contract end to end through public HTTP
    only: create Creative Brief -> create Asset (+ atomic initial
    Version) -> append a second Version -> all visible via GET."""
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    client = fixtures["client"]
    path = _assets_path(fixtures, content_public_id)

    brief_response = client.post(f"{path}/creative-brief", json={"spec": {"tone": "playful"}}, headers=headers)
    assert brief_response.status_code == 201

    asset_response = client.post(
        path, json={"kind": "image", "storage_reference": "https://example.com/v1.png"}, headers=headers
    )
    assert asset_response.status_code == 201
    asset_id = asset_response.json()["assets"][0]["id"]
    assert asset_response.json()["assets"][0]["current_version"]["storage_reference"] == "https://example.com/v1.png"

    version_response = client.post(
        f"{path}/{asset_id}/versions", json={"storage_reference": "https://example.com/v2.png"}, headers=headers
    )
    assert version_response.status_code == 201
    assert version_response.json()["assets"][0]["current_version"]["storage_reference"] == "https://example.com/v2.png"

    final = client.get(path).json()
    assert final["creative_brief"] == {"spec": {"tone": "playful"}}
    assert len(final["assets"]) == 1
    assert final["assets"][0]["current_version"]["storage_reference"] == "https://example.com/v2.png"
