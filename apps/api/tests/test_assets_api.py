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


def test_only_get_route_exists_for_assets(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    path = _assets_path(fixtures, content_public_id)
    assert fixtures["client"].get(path).status_code == 200
    assert fixtures["client"].post(path, json={}).status_code == 405
    assert fixtures["client"].put(path, json={}).status_code == 405
    assert fixtures["client"].patch(path, json={}).status_code == 405
    assert fixtures["client"].delete(path).status_code == 405


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
