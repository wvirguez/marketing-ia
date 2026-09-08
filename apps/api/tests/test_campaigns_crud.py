"""Campaign creation, atomicity, public IDs, listing, retrieval, patch,
archive, and run persistence (BACKEND-05 §9/§13-19/§24).

All marked `postgres` — real database required.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.campaigns.models import Campaign, CampaignBrief, CampaignRun
from tests.campaignstest import campaign_payload

pytestmark = pytest.mark.postgres


def _count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def _create(client: TestClient, csrf_token: str, **overrides: object):
    return client.post("/api/v1/campaigns", json=campaign_payload(**overrides), headers={"X-CSRF-Token": csrf_token})


# --- Creation, atomicity, public IDs, no UUID exposure ----------------


def test_create_campaign_returns_safe_public_objects(campaign_client: dict) -> None:
    response = _create(campaign_client["client"], campaign_client["csrf_token"])

    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {"campaign", "brief", "run"}
    assert body["campaign"]["name"] == "Método Canino en Casa"
    assert body["campaign"]["status"] == "SUBMITTED"
    assert body["campaign"]["archived_at"] is None
    assert body["brief"]["version"] == 1
    assert body["brief"]["prompt"] == campaign_payload()["prompt"]
    assert body["run"]["run_number"] == 1
    assert body["run"]["status"] == "CREATED"


def test_create_campaign_public_ids_use_the_documented_prefixes(campaign_client: dict) -> None:
    body = _create(campaign_client["client"], campaign_client["csrf_token"]).json()

    assert body["campaign"]["id"].startswith("CMP-")
    assert body["brief"]["id"].startswith("CBR-")
    assert body["run"]["id"].startswith("RUN-")
    assert body["brief"]["campaign_id"] == body["campaign"]["id"]
    assert body["run"]["campaign_id"] == body["campaign"]["id"]


def test_create_campaign_response_never_contains_a_raw_uuid(campaign_client: dict, db_session) -> None:
    response = _create(campaign_client["client"], campaign_client["csrf_token"])
    body = response.json()

    campaign = db_session.execute(
        select(Campaign).where(Campaign.public_id == body["campaign"]["id"])
    ).scalar_one()

    # The internal UUID primary key must never appear anywhere in the response.
    assert str(campaign.id) not in response.text
    assert str(campaign.workspace_id) not in response.text


def test_create_campaign_persists_exactly_one_row_in_each_table(campaign_client: dict, db_session) -> None:
    before = {model: _count(db_session, model) for model in (Campaign, CampaignBrief, CampaignRun)}

    _create(campaign_client["client"], campaign_client["csrf_token"])

    for model, before_count in before.items():
        after_count = _count(db_session, model)
        assert after_count == before_count + 1, f"{model.__tablename__} did not gain exactly one row"


def test_create_campaign_requires_csrf(campaign_client: dict) -> None:
    response = campaign_client["client"].post("/api/v1/campaigns", json=campaign_payload())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


def test_create_campaign_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.post("/api/v1/campaigns", json=campaign_payload())

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_create_campaign_rejects_blank_name(campaign_client: dict) -> None:
    response = _create(campaign_client["client"], campaign_client["csrf_token"], name="   ")
    assert response.status_code == 422


def test_create_campaign_rejects_blank_prompt(campaign_client: dict) -> None:
    response = _create(campaign_client["client"], campaign_client["csrf_token"], prompt="")
    assert response.status_code == 422


def test_create_campaign_accepts_optional_structured_context(campaign_client: dict) -> None:
    response = _create(
        campaign_client["client"],
        campaign_client["csrf_token"],
        product_type="Curso online",
        price="29 USD",
        audience="Dueños de perros primerizos",
        budget="500 USD al mes",
        channel="Instagram",
    )
    body = response.json()
    assert body["brief"]["product_type"] == "Curso online"
    assert body["brief"]["price"] == "29 USD"
    assert body["brief"]["audience"] == "Dueños de perros primerizos"
    assert body["brief"]["budget"] == "500 USD al mes"
    assert body["brief"]["channel"] == "Instagram"


def test_campaign_creation_failure_mid_transaction_leaves_no_orphan_rows(db_session) -> None:
    """Directly exercises CampaignService.create_campaign() so a forced
    failure inside the run-creation step never reaches session.commit()
    — proving the whole operation is atomic, without going through HTTP."""
    from app.campaigns.service import CampaignService
    from app.workspaces.models import Membership, Organization, Workspace
    from app.workspaces.repository import MembershipRepository, OrganizationRepository, WorkspaceRepository

    organization = OrganizationRepository(db_session).create(name="Atomic Test Org")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="Atomic Test Workspace")
    db_session.flush()

    before = {model: _count(db_session, model) for model in (Campaign, CampaignBrief, CampaignRun)}

    service = CampaignService(db_session)
    with patch(
        "app.campaigns.repository.CampaignRunRepository.create",
        side_effect=RuntimeError("simulated failure before commit"),
    ):
        with pytest.raises(RuntimeError, match="simulated failure before commit"):
            service.create_campaign(
                workspace_id=workspace.id,
                name="Should Not Persist",
                prompt="Should not persist either.",
                product_type=None,
                price=None,
                audience=None,
                budget=None,
                channel=None,
            )

    db_session.rollback()

    for model, before_count in before.items():
        after_count = _count(db_session, model)
        assert after_count == before_count, f"{model.__tablename__} has an orphan row after a failed creation"


# --- Listing / pagination ----------------------------------------------


def test_list_campaigns_returns_only_this_workspaces_campaigns(campaign_client: dict) -> None:
    client, csrf = campaign_client["client"], campaign_client["csrf_token"]
    _create(client, csrf, name="First")
    _create(client, csrf, name="Second")

    response = client.get("/api/v1/campaigns")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {item["name"] for item in body["items"]} == {"First", "Second"}


def test_list_campaigns_orders_newest_first(campaign_client: dict) -> None:
    client, csrf = campaign_client["client"], campaign_client["csrf_token"]
    _create(client, csrf, name="Older")
    _create(client, csrf, name="Newer")

    body = client.get("/api/v1/campaigns").json()

    assert [item["name"] for item in body["items"]] == ["Newer", "Older"]


def test_list_campaigns_pagination_is_bounded(campaign_client: dict) -> None:
    client, csrf = campaign_client["client"], campaign_client["csrf_token"]
    for i in range(3):
        _create(client, csrf, name=f"Campaign {i}")

    response = client.get("/api/v1/campaigns", params={"limit": 2, "offset": 0})
    body = response.json()

    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["total"] == 3


def test_list_campaigns_rejects_limit_above_the_maximum(campaign_client: dict) -> None:
    response = campaign_client["client"].get("/api/v1/campaigns", params={"limit": 10_000})
    assert response.status_code == 422


def test_list_campaigns_excludes_archived_by_default(campaign_client: dict) -> None:
    client, csrf = campaign_client["client"], campaign_client["csrf_token"]
    created = _create(client, csrf).json()["campaign"]["id"]
    client.post(f"/api/v1/campaigns/{created}/archive", headers={"X-CSRF-Token": csrf})

    default_listing = client.get("/api/v1/campaigns").json()
    assert default_listing["total"] == 0

    inclusive_listing = client.get("/api/v1/campaigns", params={"include_archived": True}).json()
    assert inclusive_listing["total"] == 1


# --- Retrieval -----------------------------------------------------------


def test_get_campaign_by_public_id(campaign_client: dict) -> None:
    client, csrf = campaign_client["client"], campaign_client["csrf_token"]
    campaign_id = _create(client, csrf).json()["campaign"]["id"]

    response = client.get(f"/api/v1/campaigns/{campaign_id}")

    assert response.status_code == 200
    assert response.json()["id"] == campaign_id


def test_get_unknown_campaign_public_id_is_forbidden_not_leaky(campaign_client: dict) -> None:
    response = campaign_client["client"].get("/api/v1/campaigns/CMP-DOESNOTEXIST0")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


# --- Patch -----------------------------------------------------------------


def test_patch_campaign_updates_the_name(campaign_client: dict) -> None:
    client, csrf = campaign_client["client"], campaign_client["csrf_token"]
    campaign_id = _create(client, csrf).json()["campaign"]["id"]

    response = client.patch(
        f"/api/v1/campaigns/{campaign_id}", json={"name": "Renamed Campaign"}, headers={"X-CSRF-Token": csrf}
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed Campaign"


def test_patch_campaign_requires_csrf(campaign_client: dict) -> None:
    client, csrf = campaign_client["client"], campaign_client["csrf_token"]
    campaign_id = _create(client, csrf).json()["campaign"]["id"]

    response = client.patch(f"/api/v1/campaigns/{campaign_id}", json={"name": "No CSRF"})

    assert response.status_code == 403


def test_patch_campaign_cannot_set_forbidden_fields(campaign_client: dict) -> None:
    """Only `name` is accepted by the schema at all — sending anything
    else (status, workspace ownership, ids, timestamps) either has no
    effect (extra fields are dropped) or the request never reaches a
    place that could use them, since CampaignPatchRequest has no such
    field to bind to."""
    client, csrf = campaign_client["client"], campaign_client["csrf_token"]
    created = _create(client, csrf).json()["campaign"]
    campaign_id = created["id"]

    response = client.patch(
        f"/api/v1/campaigns/{campaign_id}",
        json={
            "name": "Still Renamed",
            "status": "COMPLETED",
            "id": "CMP-HIJACKEDXXXX",
            "archived_at": "2020-01-01T00:00:00Z",
            "created_at": "2020-01-01T00:00:00Z",
        },
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Still Renamed"
    assert body["status"] == "SUBMITTED"  # unchanged
    assert body["id"] == campaign_id  # unchanged
    assert body["archived_at"] is None  # unchanged
    assert body["created_at"] == created["created_at"]  # unchanged


# --- Archive ---------------------------------------------------------------


def test_archive_campaign_is_non_destructive(campaign_client: dict, db_session) -> None:
    client, csrf = campaign_client["client"], campaign_client["csrf_token"]
    campaign_id = _create(client, csrf).json()["campaign"]["id"]

    response = client.post(f"/api/v1/campaigns/{campaign_id}/archive", headers={"X-CSRF-Token": csrf})

    assert response.status_code == 200
    assert response.json()["archived_at"] is not None

    stored = db_session.execute(select(Campaign).where(Campaign.public_id == campaign_id)).scalar_one()
    assert stored.archived_at is not None
    # The row still exists — archiving is non-destructive.
    assert db_session.execute(select(CampaignBrief).where(CampaignBrief.campaign_id == stored.id)).scalar_one()
    assert db_session.execute(select(CampaignRun).where(CampaignRun.campaign_id == stored.id)).scalar_one()


def test_archive_campaign_requires_csrf(campaign_client: dict) -> None:
    client, csrf = campaign_client["client"], campaign_client["csrf_token"]
    campaign_id = _create(client, csrf).json()["campaign"]["id"]

    response = client.post(f"/api/v1/campaigns/{campaign_id}/archive")
    assert response.status_code == 403


def test_archive_campaign_is_idempotent(campaign_client: dict) -> None:
    client, csrf = campaign_client["client"], campaign_client["csrf_token"]
    campaign_id = _create(client, csrf).json()["campaign"]["id"]

    first = client.post(f"/api/v1/campaigns/{campaign_id}/archive", headers={"X-CSRF-Token": csrf}).json()
    second = client.post(f"/api/v1/campaigns/{campaign_id}/archive", headers={"X-CSRF-Token": csrf}).json()

    # Same instant, potentially formatted with a different UTC offset
    # (the first response serializes the in-memory value just set with
    # `datetime.now(timezone.utc)`; the second re-reads it from
    # PostgreSQL, which round-trips it with the connection's own tzinfo)
    # — compare as actual datetimes, not as strings.
    assert datetime.fromisoformat(first["archived_at"]) == datetime.fromisoformat(second["archived_at"])


# --- Runs --------------------------------------------------------------


def test_list_campaign_runs_returns_the_initial_run(campaign_client: dict) -> None:
    client, csrf = campaign_client["client"], campaign_client["csrf_token"]
    campaign_id = _create(client, csrf).json()["campaign"]["id"]

    response = client.get(f"/api/v1/campaigns/{campaign_id}/runs")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["run_number"] == 1
    assert body["items"][0]["status"] == "CREATED"
    assert body["items"][0]["id"].startswith("RUN-")


def test_list_campaign_runs_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.get("/api/v1/campaigns/CMP-SOMEVALUE0000/runs")
    assert response.status_code == 401


def test_campaign_brief_version_uniqueness_is_enforced_at_the_database_level(db_session) -> None:
    """Two Campaign Brief rows at the same (campaign_id, version) must be
    rejected by the database's own unique constraint — proving the
    versioning invariant is not just an application-level convention."""
    from app.workspaces.repository import OrganizationRepository, WorkspaceRepository

    organization = OrganizationRepository(db_session).create(name="Uniqueness Test Org")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="Uniqueness Test WS")
    campaign = Campaign(public_id="CMP-UNIQUETEST01", workspace_id=workspace.id, name="Uniqueness Test")
    db_session.add(campaign)
    db_session.flush()

    db_session.add(CampaignBrief(public_id="CBR-UNIQUETEST01", campaign_id=campaign.id, version=1, prompt="first"))
    db_session.flush()

    db_session.add(CampaignBrief(public_id="CBR-UNIQUETEST02", campaign_id=campaign.id, version=1, prompt="dup"))
    with pytest.raises(IntegrityError):
        db_session.flush()
