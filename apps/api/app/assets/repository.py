"""Data access for CreativeBrief / Asset / AssetVersion. No repository
here calls ``session.commit()`` — see ``app/persistence/session.py`` and
``app/assets/service.py`` for the transaction-ownership boundary.

Every ``create`` method takes the parent domain object (``ContentPiece``,
``CreativeBrief``, ``Asset``), never a raw ``workspace_id``/
``content_piece_id``/``creative_brief_id`` parameter — the same
"no independent parameter, no possibility of drift" pattern established in
``app/content/repository.py``/``app/measurement/repository.py``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assets.models import Asset, AssetVersion, CreativeBrief
from app.content.models import ContentPiece
from app.core.ids import generate_public_id


class CreativeBriefRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, content_piece: ContentPiece, spec: dict) -> CreativeBrief:
        row = CreativeBrief(workspace_id=content_piece.workspace_id, content_piece_id=content_piece.id, spec=spec)
        self.session.add(row)
        self.session.flush()
        return row

    def get_for_content_piece(self, content_piece_id: uuid.UUID) -> CreativeBrief | None:
        return self.session.execute(
            select(CreativeBrief).where(CreativeBrief.content_piece_id == content_piece_id)
        ).scalar_one_or_none()

    def get_by_id(self, creative_brief_id: uuid.UUID) -> CreativeBrief | None:
        return self.session.get(CreativeBrief, creative_brief_id)


class AssetRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, creative_brief: CreativeBrief, kind: str, status: str | None) -> Asset:
        row = Asset(
            public_id=generate_public_id("AST"),
            workspace_id=creative_brief.workspace_id,
            creative_brief_id=creative_brief.id,
            kind=kind,
            status=status,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_public_id(self, public_id: str, *, for_update: bool = False) -> Asset | None:
        query = select(Asset).where(Asset.public_id == public_id)
        if for_update:
            query = query.with_for_update()
        return self.session.execute(query).scalar_one_or_none()

    def get_by_id(self, asset_id: uuid.UUID) -> Asset | None:
        return self.session.get(Asset, asset_id)

    def list_active_for_content_piece(self, content_piece_id: uuid.UUID) -> list[Asset]:
        """Non-archived Assets belonging to the given ContentPiece's (0..1)
        CreativeBrief. A ContentPiece with no CreativeBrief yet, or none
        at all, correctly yields an empty list, never an error."""
        return list(
            self.session.execute(
                select(Asset)
                .join(CreativeBrief, Asset.creative_brief_id == CreativeBrief.id)
                .where(CreativeBrief.content_piece_id == content_piece_id, Asset.archived_at.is_(None))
                .order_by(Asset.created_at.asc(), Asset.id.asc())
            )
            .scalars()
            .all()
        )


class AssetVersionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, asset: Asset, storage_reference: str | None, metadata: dict) -> AssetVersion:
        row = AssetVersion(asset_id=asset.id, storage_reference=storage_reference, metadata_=metadata)
        self.session.add(row)
        self.session.flush()
        return row

    def get_latest_for_asset(self, asset_id: uuid.UUID) -> AssetVersion | None:
        """Deterministic "current version" selection (frozen, Phase 1C):
        no ordinal column exists — ordered by ``created_at DESC, id
        DESC``."""
        return self.session.execute(
            select(AssetVersion)
            .where(AssetVersion.asset_id == asset_id)
            .order_by(AssetVersion.created_at.desc(), AssetVersion.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    def list_for_asset(self, asset_id: uuid.UUID) -> list[AssetVersion]:
        return list(
            self.session.execute(
                select(AssetVersion)
                .where(AssetVersion.asset_id == asset_id)
                .order_by(AssetVersion.created_at.asc(), AssetVersion.id.asc())
            )
            .scalars()
            .all()
        )
