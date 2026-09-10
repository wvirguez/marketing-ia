"""Assets domain persistence, provenance, tenancy, cardinality, ordering,
and governance-boundary tests (BACKEND-13). All marked `postgres`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from app.assets.models import Asset, AssetVersion, CreativeBrief
from app.assets.service import AssetsService
from app.core.api_errors import AssetArchivedError, CreativeBriefAlreadyExistsError, ForbiddenError
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository
from tests.assetstest import (
    build_asset,
    build_content_piece,
    build_creative_brief,
    default_asset_fields,
    default_creative_brief_spec,
)

pytestmark = pytest.mark.postgres


# --- CreativeBrief: domain persistence -------------------------------


def test_creative_brief_persists_with_correct_workspace_and_no_public_id(db_session) -> None:
    campaign, piece, brief = build_creative_brief(db_session)
    assert brief.workspace_id == campaign.workspace_id
    assert brief.content_piece_id == piece.id
    assert "public_id" not in [c.lower() for c in CreativeBrief.__table__.columns.keys()]


def test_duplicate_creative_brief_for_same_content_piece_is_rejected(db_session) -> None:
    _campaign, piece, _brief = build_creative_brief(db_session)
    with pytest.raises(CreativeBriefAlreadyExistsError):
        AssetsService(db_session).record_creative_brief(content_piece=piece, spec=default_creative_brief_spec())


def test_creative_brief_workspace_mismatch_with_content_piece_rejected_at_db_level(db_session) -> None:
    """Bypasses the service entirely — direct model construction proves
    the composite FK itself rejects a workspace mismatch."""
    _campaign, _content_campaign, _content_brief, piece, _version = build_content_piece(db_session)
    other_workspace = WorkspaceRepository(db_session).create(
        organization_id=OrganizationRepository(db_session).create(name="Rogue Org").id, name="Rogue WS"
    )
    db_session.flush()

    rogue = CreativeBrief(workspace_id=other_workspace.id, content_piece_id=piece.id, spec={})
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_no_forbidden_fields_exist_on_creative_brief() -> None:
    forbidden = (
        "public_id", "updated_at", "status", "archived_at", "version", "campaign_id", "campaign_run_id",
        "stage_execution_id",
    )
    columns = [c.lower() for c in CreativeBrief.__table__.columns.keys()]
    for term in forbidden:
        assert term not in columns, f"unexpected field {term!r} on CreativeBrief"


def test_no_creative_brief_replace_or_update_method_exists() -> None:
    forbidden_methods = ("update_creative_brief", "replace_creative_brief", "revise_creative_brief")
    for method in forbidden_methods:
        assert not hasattr(AssetsService, method), f"unexpected method {method!r} on AssetsService"


# --- Asset: domain persistence -----------------------------------------


def test_asset_and_initial_version_persist_atomically(db_session) -> None:
    _campaign, _piece, creative_brief, asset, version = build_asset(db_session)
    assert asset.public_id.startswith("AST-")
    assert asset.workspace_id == creative_brief.workspace_id
    assert asset.creative_brief_id == creative_brief.id
    assert asset.status is None
    assert asset.archived_at is None
    assert version.asset_id == asset.id


def test_asset_workspace_mismatch_with_creative_brief_rejected_at_db_level(db_session) -> None:
    _campaign, _piece, creative_brief = build_creative_brief(db_session)
    other_workspace = WorkspaceRepository(db_session).create(
        organization_id=OrganizationRepository(db_session).create(name="Rogue Org 2").id, name="Rogue WS 2"
    )
    db_session.flush()

    rogue = Asset(public_id="AST-MISMATCHTEST", workspace_id=other_workspace.id, creative_brief_id=creative_brief.id, kind="image")
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_creative_brief_can_own_multiple_assets(db_session) -> None:
    _campaign, _piece, creative_brief = build_creative_brief(db_session)
    service = AssetsService(db_session)
    asset_a, _va = service.record_asset(creative_brief=creative_brief, **default_asset_fields())
    asset_b, _vb = service.record_asset(creative_brief=creative_brief, **default_asset_fields(kind="video"))
    assert asset_a.id != asset_b.id
    assert asset_a.creative_brief_id == asset_b.creative_brief_id == creative_brief.id


def test_asset_kind_is_open_bounded_string_not_enum() -> None:
    column = Asset.__table__.columns["kind"]
    assert column.type.python_type is str


def test_asset_status_is_nullable() -> None:
    column = Asset.__table__.columns["status"]
    assert column.nullable is True


def test_no_forbidden_fields_exist_on_asset() -> None:
    forbidden = (
        "content_version_id", "content_piece_id", "campaign_id", "campaign_run_id", "stage_execution_id", "agent_id",
    )
    columns = [c.lower() for c in Asset.__table__.columns.keys()]
    for term in forbidden:
        assert term not in columns, f"unexpected field {term!r} on Asset"


# --- Asset: archive semantics -------------------------------------------


def test_archive_sets_archived_at_and_is_idempotent(db_session) -> None:
    campaign, _piece, _creative_brief, asset, _version = build_asset(db_session)
    service = AssetsService(db_session)
    archived_once = service.archive_asset(workspace_id=campaign.workspace_id, asset_public_id=asset.public_id)
    assert archived_once.archived_at is not None
    first_archived_at = archived_once.archived_at

    archived_twice = service.archive_asset(workspace_id=campaign.workspace_id, asset_public_id=asset.public_id)
    assert archived_twice.archived_at == first_archived_at


def test_archived_asset_excluded_from_active_listing(db_session) -> None:
    campaign, piece, _creative_brief, asset, _version = build_asset(db_session)
    service = AssetsService(db_session)
    assert len(service.list_active_assets_for_content_piece(content_piece_id=piece.id)) == 1
    service.archive_asset(workspace_id=campaign.workspace_id, asset_public_id=asset.public_id)
    assert service.list_active_assets_for_content_piece(content_piece_id=piece.id) == []


def test_archive_does_not_mutate_status_or_creative_brief_or_history(db_session) -> None:
    campaign, _piece, creative_brief, asset, version = build_asset(db_session)
    service = AssetsService(db_session)
    service.archive_asset(workspace_id=campaign.workspace_id, asset_public_id=asset.public_id)
    db_session.refresh(asset)
    db_session.refresh(creative_brief)
    assert asset.status is None
    assert creative_brief.spec == default_creative_brief_spec()
    reloaded_version = db_session.execute(select(AssetVersion).where(AssetVersion.id == version.id)).scalar_one()
    assert reloaded_version.storage_reference == version.storage_reference


def test_archive_of_unknown_asset_is_forbidden(db_session) -> None:
    campaign, *_ = build_content_piece(db_session)
    with pytest.raises(ForbiddenError):
        AssetsService(db_session).archive_asset(workspace_id=campaign.workspace_id, asset_public_id="AST-DOESNOTEXIST")


def test_archive_of_another_workspaces_asset_is_forbidden(db_session) -> None:
    _campaign, _piece, _creative_brief, asset, _version = build_asset(db_session)
    other_campaign, *_ = build_content_piece(db_session, campaign_name="Other Campaign")
    with pytest.raises(ForbiddenError):
        AssetsService(db_session).archive_asset(workspace_id=other_campaign.workspace_id, asset_public_id=asset.public_id)


# --- AssetVersion: append, immutability, tenancy ------------------------


def test_asset_version_has_no_workspace_id_public_id_or_ordinal_column() -> None:
    columns = [c.lower() for c in AssetVersion.__table__.columns.keys()]
    assert "workspace_id" not in columns
    assert "public_id" not in columns
    for term in ("ordinal", "sequence", "version_number"):
        assert not any(term in c for c in columns), f"unexpected field containing {term!r} on AssetVersion"


def test_asset_version_cannot_reference_nonexistent_asset(db_session) -> None:
    rogue = AssetVersion(asset_id=uuid.uuid4(), metadata_={})
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_storage_reference_nullable_opaque_roundtrip(db_session) -> None:
    campaign, _piece, creative_brief, _asset, _v = build_asset(db_session)
    asset, version = AssetsService(db_session).record_asset(
        creative_brief=creative_brief, kind="video", storage_reference=None, metadata={}
    )
    assert version.storage_reference is None


def test_arbitrary_metadata_jsonb_roundtrip(db_session) -> None:
    payload = {"width": 1920, "height": 1080, "tags": ["ugc", "vertical"], "nested": {"a": 1}}
    _campaign, _piece, creative_brief, _asset, _v = build_asset(db_session)
    asset, version = AssetsService(db_session).record_asset(
        creative_brief=creative_brief, kind="video", metadata=payload
    )
    db_session.refresh(version)
    assert version.metadata_ == payload


def test_append_version_creates_immutable_history(db_session) -> None:
    campaign, _piece, _creative_brief, asset, v1 = build_asset(db_session)
    service = AssetsService(db_session)
    v2 = service.record_asset_version(workspace_id=campaign.workspace_id, asset_public_id=asset.public_id, metadata={"n": 2})
    v3 = service.record_asset_version(workspace_id=campaign.workspace_id, asset_public_id=asset.public_id, metadata={"n": 3})

    history = service.versions.list_for_asset(asset.id)
    # Order is not asserted here — all three inserts share one Postgres
    # transaction under the rollback-isolated `db_session` fixture, so
    # `now()` (the frozen ordering timestamp, Phase 1C) ties; exact
    # cross-transaction ordering is covered separately, below, using
    # genuinely independent committed transactions.
    assert {v.id for v in history} == {v1.id, v2.id, v3.id}


def test_append_version_rejected_on_archived_asset(db_session) -> None:
    campaign, _piece, _creative_brief, asset, _v = build_asset(db_session)
    service = AssetsService(db_session)
    service.archive_asset(workspace_id=campaign.workspace_id, asset_public_id=asset.public_id)
    with pytest.raises(AssetArchivedError):
        service.record_asset_version(workspace_id=campaign.workspace_id, asset_public_id=asset.public_id)


def test_current_version_selected_by_created_at_then_id_across_separate_transactions(postgres_engine) -> None:
    """Ordering micro-gate (Phase 1C): append a later AssetVersion in a
    genuinely separate, independently-committed transaction and confirm
    it becomes current — no sleep, no mocked timestamp, no production
    timestamp function change (§31). Deliberately does not use the
    rollback-isolated `db_session` fixture at all: every session here is
    bound directly to `postgres_engine` with a real, independent commit,
    the same "genuinely separate transaction" pattern
    `tests/test_settings_concurrency.py` already establishes — a
    `db_session`-backed write is never truly committed to the database
    (only rolled back at teardown), so a second connection could never
    observe it regardless of which timestamp function were used."""
    with OrmSession(bind=postgres_engine) as session:
        campaign, _piece, _creative_brief, asset, v1 = build_asset(session)
        session.commit()
        workspace_id, asset_id, asset_public_id, v1_id = campaign.workspace_id, asset.id, asset.public_id, v1.id

    with OrmSession(bind=postgres_engine) as session:
        v2 = AssetsService(session).record_asset_version(
            workspace_id=workspace_id, asset_public_id=asset_public_id, metadata={"n": 2}
        )
        v2_id = v2.id

    with OrmSession(bind=postgres_engine) as session:
        current = AssetsService(session).get_current_version_for_asset(asset_id)
        assert current.id == v2_id
        assert current.id != v1_id


def test_equal_created_at_tie_breaks_deterministically_by_id_not_chronology(db_session) -> None:
    """A defensive tie test only — proves id DESC is a deterministic
    tie-breaker, never asserts UUIDv4 ordering is chronological (§31)."""
    _campaign, _piece, _creative_brief, asset, v1 = build_asset(db_session)
    v2 = AssetVersion(asset_id=asset.id, metadata_={})
    db_session.add(v2)
    db_session.flush()
    # Force an exact tie on created_at to isolate the id-tiebreaker path.
    v2.created_at = v1.created_at
    db_session.flush()

    from app.assets.repository import AssetVersionRepository

    current = AssetVersionRepository(db_session).get_latest_for_asset(asset.id)
    assert current.id == max(v1.id, v2.id)


# --- governance: no forbidden methods, no forbidden tables ----------------


def test_assets_service_exposes_no_storage_ai_or_distribution_methods() -> None:
    forbidden_methods = (
        "upload_asset", "generate_image", "generate_video", "presign_upload", "publish_asset",
        "distribute_asset", "mark_ready_for_distribution", "restore_asset", "unarchive_asset", "delete_asset",
    )
    for method in forbidden_methods:
        assert not hasattr(AssetsService, method), f"unexpected method {method!r} on AssetsService"


def test_no_forbidden_tables_were_introduced() -> None:
    from app.persistence.base import metadata

    table_names = set(metadata.tables.keys())
    for forbidden_table in ("content_revision_requests", "distribution_plans", "paid_media_plans", "creative_brief_versions"):
        assert forbidden_table not in table_names


def test_content_piece_gained_only_the_authorized_candidate_key() -> None:
    from app.content.models import ContentPiece

    constraint_names = {c.name for c in ContentPiece.__table__.constraints if getattr(c, "name", None)}
    assert "uq_content_pieces_id_workspace_id" in constraint_names


# --- cross-domain protection ---------------------------------------------


def test_recording_asset_leaves_content_piece_status_unchanged(db_session) -> None:
    from app.content.models import ContentPieceStatus

    _campaign, piece, _creative_brief, _asset, _v = build_asset(db_session)
    db_session.refresh(piece)
    assert piece.status is ContentPieceStatus.DRAFT
