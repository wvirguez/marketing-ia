"""Audit attribution and atomicity for Assets persistence (BACKEND-13
§18/§19; HTTP-triggered creation attribution added by MVP-16B). All
marked `postgres`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.assets.models import Asset, AssetVersion, CreativeBrief
from app.assets.repository import AssetVersionRepository
from app.assets.service import AssetsService
from app.audit.models import ActorType, AuditEvent
from app.audit.repository import AuditEventRepository
from app.content.service import ContentService
from app.persistence.session import get_engine
from tests.assetstest import build_asset, build_content_piece, build_creative_brief, default_asset_fields, default_creative_brief_spec
from tests.test_assets_api import _assets_path, _record_content_piece, campaign_client_with_stages

pytestmark = pytest.mark.postgres


def _total_count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


# --- exact attribution ------------------------------------------------


def test_creative_brief_recorded_event_identifies_the_exact_brief(db_session) -> None:
    _campaign, _piece, brief = build_creative_brief(db_session)
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "assets.creative_brief.recorded")
    ).scalars().all()
    matching = [e for e in events if e.creative_brief_id == brief.id]
    assert len(matching) == 1


def test_asset_and_version_recorded_events_identify_exact_rows_and_are_two_separate_events(db_session) -> None:
    _campaign, _piece, _creative_brief, asset, version = build_asset(db_session)

    asset_events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "assets.asset.recorded", AuditEvent.asset_id == asset.id)
    ).scalars().all()
    assert len(asset_events) == 1

    version_events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "assets.asset_version.recorded", AuditEvent.asset_version_id == version.id
        )
    ).scalars().all()
    assert len(version_events) == 1
    assert version_events[0].asset_id == asset.id
    assert asset_events[0].id != version_events[0].id


def test_two_assets_created_close_together_are_never_confused(db_session) -> None:
    _campaign, _piece, creative_brief = build_creative_brief(db_session)
    service = AssetsService(db_session)
    asset_a, _va = service.record_asset(creative_brief=creative_brief, **default_asset_fields())
    asset_b, _vb = service.record_asset(creative_brief=creative_brief, **default_asset_fields(kind="video"))

    events = db_session.execute(select(AuditEvent).where(AuditEvent.event_type == "assets.asset.recorded")).scalars().all()
    matching_a = [e for e in events if e.asset_id == asset_a.id]
    matching_b = [e for e in events if e.asset_id == asset_b.id]
    assert len(matching_a) == 1
    assert len(matching_b) == 1
    assert matching_a[0].id != matching_b[0].id


def test_appended_version_recorded_event_identifies_exact_version(db_session) -> None:
    campaign, _piece, _creative_brief, asset, _v1 = build_asset(db_session)
    v2 = AssetsService(db_session).record_asset_version(workspace_id=campaign.workspace_id, asset_public_id=asset.public_id)
    events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "assets.asset_version.recorded", AuditEvent.asset_version_id == v2.id
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].asset_id == asset.id


def test_archive_emits_exactly_one_event_no_duplicate_on_second_archive(db_session) -> None:
    campaign, _piece, _creative_brief, asset, _v = build_asset(db_session)
    service = AssetsService(db_session)
    service.archive_asset(workspace_id=campaign.workspace_id, asset_public_id=asset.public_id)
    service.archive_asset(workspace_id=campaign.workspace_id, asset_public_id=asset.public_id)

    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "assets.asset.archived", AuditEvent.asset_id == asset.id)
    ).scalars().all()
    assert len(events) == 1


# --- atomicity: rollback on failure --------------------------------------


def test_audit_failure_rolls_back_the_creative_brief(db_session) -> None:
    _campaign, _content_campaign, _content_brief, piece, _version = build_content_piece(db_session)
    briefs_before = _total_count(db_session, CreativeBrief)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            AssetsService(db_session).record_creative_brief(content_piece=piece, spec=default_creative_brief_spec())

    db_session.rollback()
    assert _total_count(db_session, CreativeBrief) == briefs_before


def test_audit_failure_rolls_back_asset_and_initial_version_together(db_session) -> None:
    _campaign, _piece, creative_brief = build_creative_brief(db_session)
    assets_before = _total_count(db_session, Asset)
    versions_before = _total_count(db_session, AssetVersion)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            AssetsService(db_session).record_asset(creative_brief=creative_brief, **default_asset_fields())

    db_session.rollback()
    assert _total_count(db_session, Asset) == assets_before
    assert _total_count(db_session, AssetVersion) == versions_before


def test_child_version_failure_rolls_back_the_parent_asset_too(db_session) -> None:
    _campaign, _piece, creative_brief = build_creative_brief(db_session)
    assets_before = _total_count(db_session, Asset)

    with patch.object(AssetVersionRepository, "create", side_effect=RuntimeError("simulated version failure")):
        with pytest.raises(RuntimeError, match="simulated version failure"):
            AssetsService(db_session).record_asset(creative_brief=creative_brief, **default_asset_fields())

    db_session.rollback()
    assert _total_count(db_session, Asset) == assets_before


def test_audit_failure_rolls_back_an_appended_version(db_session) -> None:
    campaign, _piece, _creative_brief, asset, _v1 = build_asset(db_session)
    versions_before = _total_count(db_session, AssetVersion)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            AssetsService(db_session).record_asset_version(workspace_id=campaign.workspace_id, asset_public_id=asset.public_id)

    db_session.rollback()
    assert _total_count(db_session, AssetVersion) == versions_before


def test_audit_failure_rolls_back_the_archive(db_session) -> None:
    campaign, _piece, _creative_brief, asset, _v = build_asset(db_session)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            AssetsService(db_session).archive_asset(workspace_id=campaign.workspace_id, asset_public_id=asset.public_id)

    db_session.rollback()
    db_session.refresh(asset)
    assert asset.archived_at is None


# --- HTTP-triggered creation attribution (MVP-16B) --------------------


def test_http_created_creative_brief_is_attributed_to_the_real_user_not_system(
    campaign_client_with_stages: dict,
) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    response = fixtures["client"].post(
        f"{_assets_path(fixtures, content_public_id)}/creative-brief",
        json={"spec": {}},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 201, response.text

    with OrmSession(bind=get_engine()) as session:
        brief = session.execute(
            select(CreativeBrief).where(CreativeBrief.content_piece_id.isnot(None))
        ).scalars().all()
        # Scoped precisely: the brief just created for this content piece.
        piece = ContentService(session).pieces.get_by_public_id(content_public_id)
        matching = [b for b in brief if b.content_piece_id == piece.id]
        assert len(matching) == 1
        events = session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == "assets.creative_brief.recorded", AuditEvent.creative_brief_id == matching[0].id
            )
        ).scalars().all()
        assert len(events) == 1
        assert events[0].actor_type is ActorType.USER
        assert events[0].actor_user_id is not None


def test_http_created_asset_is_attributed_to_the_real_user_not_system(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    path = _assets_path(fixtures, content_public_id)
    fixtures["client"].post(f"{path}/creative-brief", json={"spec": {}}, headers=headers)
    response = fixtures["client"].post(path, json={"kind": "image"}, headers=headers)
    assert response.status_code == 201, response.text
    asset_public_id = response.json()["assets"][0]["id"]

    with OrmSession(bind=get_engine()) as session:
        asset = session.execute(select(Asset).where(Asset.public_id == asset_public_id)).scalar_one()
        events = session.execute(
            select(AuditEvent).where(AuditEvent.event_type == "assets.asset.recorded", AuditEvent.asset_id == asset.id)
        ).scalars().all()
        assert len(events) == 1
        assert events[0].actor_type is ActorType.USER
        assert events[0].actor_user_id is not None


def test_http_appended_asset_version_is_attributed_to_the_real_user_not_system(
    campaign_client_with_stages: dict,
) -> None:
    fixtures = campaign_client_with_stages
    content_public_id = _record_content_piece(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    path = _assets_path(fixtures, content_public_id)
    fixtures["client"].post(f"{path}/creative-brief", json={"spec": {}}, headers=headers)
    created = fixtures["client"].post(path, json={"kind": "image"}, headers=headers)
    asset_public_id = created.json()["assets"][0]["id"]

    response = fixtures["client"].post(f"{path}/{asset_public_id}/versions", json={}, headers=headers)
    assert response.status_code == 201, response.text

    with OrmSession(bind=get_engine()) as session:
        asset = session.execute(select(Asset).where(Asset.public_id == asset_public_id)).scalar_one()
        versions = session.execute(select(AssetVersion).where(AssetVersion.asset_id == asset.id)).scalars().all()
        assert len(versions) == 2
        latest = max(versions, key=lambda v: (v.created_at, v.id))
        events = session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == "assets.asset_version.recorded", AuditEvent.asset_version_id == latest.id
            )
        ).scalars().all()
        assert len(events) == 1
        assert events[0].actor_type is ActorType.USER
        assert events[0].actor_user_id is not None
