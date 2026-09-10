"""Assets persistence — BACKEND-13.

No public HTTP write endpoint exists anywhere in this module — the frozen
API surface (§15 of the Governance Freeze) is GET-only. Every write here
is service-layer-only, the same shape ``ContentService``/
``MeasurementService`` already established: tests and any future,
separately-authorized caller invoke these methods directly.

Two different calling shapes, deliberately, mirroring ``ContentService``:

1. ``record_creative_brief``/``record_asset`` take **already-loaded parent
   domain objects** (``ContentPiece``, ``CreativeBrief``) — the caller
   (a test, or a future orchestration runtime) already resolved and
   authorized them. ``CreativeBrief`` itself has no ``public_id`` and is
   never independently addressable, so it can only ever be passed this
   way — there is no lookup-by-id path for it at all.
2. ``record_asset_version``/``archive_asset`` take a ``workspace_id`` plus
   an ``Asset`` public id and re-verify tenant ownership themselves before
   mutating — the same defensive shape ``ContentService``'s own transition
   methods use for mutation of an existing, independently-addressable row.

ASSET EXISTS != CONTENT APPROVED. ASSET EXISTS != READY FOR DISTRIBUTION.
ASSET EXISTS != DISTRIBUTED. Nothing here ever mutates ``ContentPiece``,
``ContentApproval``, or any orchestration/measurement table.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.assets.models import Asset, AssetVersion, CreativeBrief
from app.assets.repository import AssetRepository, AssetVersionRepository, CreativeBriefRepository
from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.content.models import ContentPiece
from app.core.api_errors import AssetArchivedError, CreativeBriefAlreadyExistsError, ForbiddenError

EVENT_CREATIVE_BRIEF_RECORDED = "assets.creative_brief.recorded"
EVENT_ASSET_RECORDED = "assets.asset.recorded"
EVENT_ASSET_VERSION_RECORDED = "assets.asset_version.recorded"
EVENT_ASSET_ARCHIVED = "assets.asset.archived"


class AssetsService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.creative_briefs = CreativeBriefRepository(session)
        self.assets = AssetRepository(session)
        self.versions = AssetVersionRepository(session)
        self.events = AuditEventRepository(session)

    # --- reads (GET-only public surface calls these) -----------------

    def list_active_assets_for_content_piece(self, *, content_piece_id: uuid.UUID) -> list[Asset]:
        return self.assets.list_active_for_content_piece(content_piece_id)

    def get_current_version_for_asset(self, asset_id: uuid.UUID) -> AssetVersion | None:
        return self.versions.get_latest_for_asset(asset_id)

    # --- Creative Brief: service-layer only, no public route, 0..1 ----

    def record_creative_brief(
        self,
        *,
        content_piece: ContentPiece,
        spec: dict,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> CreativeBrief:
        """0..1 per ContentPiece (frozen, Phase 1B-R) — immutable, never
        replaced. ``UNIQUE(content_piece_id)`` is the final race-safe
        protection, the same "try, catch IntegrityError" discipline
        ``ContentService.record_brief`` already applies for the analogous
        Plan-Item-already-briefed invariant."""
        try:
            brief = self.creative_briefs.create(content_piece=content_piece, spec=spec)
        except IntegrityError:
            self.session.rollback()
            raise CreativeBriefAlreadyExistsError() from None

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=content_piece.workspace_id,
            event_type=EVENT_CREATIVE_BRIEF_RECORDED,
            actor_type=actor_type,
            creative_brief_id=brief.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return brief

    # --- Asset + initial AssetVersion: atomic --------------------------

    def record_asset(
        self,
        *,
        creative_brief: CreativeBrief,
        kind: str,
        status: str | None = None,
        storage_reference: str | None = None,
        metadata: dict | None = None,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> tuple[Asset, AssetVersion]:
        """Atomically creates Asset + its initial AssetVersion + one
        AuditEvent per created entity (two total, never combined — the
        same "creation" shape ``ContentService.record_piece`` already
        establishes). An Asset is never left without at least one Version
        from this path — if anything raises before the final commit,
        nothing persists."""
        asset = self.assets.create(creative_brief=creative_brief, kind=kind, status=status)
        version = self.versions.create(asset=asset, storage_reference=storage_reference, metadata=metadata or {})

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=creative_brief.workspace_id,
            event_type=EVENT_ASSET_RECORDED,
            actor_type=actor_type,
            creative_brief_id=creative_brief.id,
            asset_id=asset.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.events.record(
            workspace_id=creative_brief.workspace_id,
            event_type=EVENT_ASSET_VERSION_RECORDED,
            actor_type=actor_type,
            asset_id=asset.id,
            asset_version_id=version.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return asset, version

    # --- append AssetVersion: service-layer only -----------------------

    def _load_asset_for_workspace(
        self, *, workspace_id: uuid.UUID, asset_public_id: str, for_update: bool = False
    ) -> Asset:
        asset = self.assets.get_by_public_id(asset_public_id, for_update=for_update)
        if asset is None or asset.workspace_id != workspace_id:
            raise ForbiddenError()
        return asset

    def record_asset_version(
        self,
        *,
        workspace_id: uuid.UUID,
        asset_public_id: str,
        storage_reference: str | None = None,
        metadata: dict | None = None,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> AssetVersion:
        """Immutable, append-only — never updates or deletes a prior
        AssetVersion, never writes a ``current_version`` pointer column.
        An archived Asset rejects new versions by default (§13) — archive
        removes it from active normal use."""
        asset = self._load_asset_for_workspace(workspace_id=workspace_id, asset_public_id=asset_public_id, for_update=True)
        if asset.archived_at is not None:
            raise AssetArchivedError()

        version = self.versions.create(asset=asset, storage_reference=storage_reference, metadata=metadata or {})
        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=asset.workspace_id,
            event_type=EVENT_ASSET_VERSION_RECORDED,
            actor_type=actor_type,
            asset_id=asset.id,
            asset_version_id=version.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return version

    # --- archive: idempotent, no restore, no hard delete ---------------

    def archive_asset(
        self,
        *,
        workspace_id: uuid.UUID,
        asset_public_id: str,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> Asset:
        """Idempotent: a second archive of an already-archived Asset is a
        pure no-op — no second ``assets.asset.archived`` event, no error.
        AssetVersion history and CreativeBrief remain untouched;
        ``Asset.status`` is not mutated by this method."""
        asset = self._load_asset_for_workspace(workspace_id=workspace_id, asset_public_id=asset_public_id, for_update=True)
        if asset.archived_at is not None:
            return asset

        asset.archived_at = datetime.now(timezone.utc)
        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=asset.workspace_id,
            event_type=EVENT_ASSET_ARCHIVED,
            actor_type=actor_type,
            asset_id=asset.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return asset
