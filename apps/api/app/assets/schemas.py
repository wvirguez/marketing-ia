"""Public DTOs for the Assets read surface. Never expose an internal
UUID, a raw ``workspace_id``, or a ``CreativeBrief``/``AssetVersion``
internal id (neither has a ``public_id`` — see ``app/assets/models.py``).
``CreativeBrief`` exposes only its ``spec`` payload — never its ``id``,
``workspace_id``, or ``content_piece_id`` (BACKEND-13 Governance Freeze:
the frozen response contract names ``creative_brief: {"spec": ...} |
null`` alongside ``assets: [...]``).

No field here ever represents distribution readiness, content approval
authority, or a chain-of-thought/reasoning trace. Actor attribution lives
in the Audit Event trail, not this domain read model, matching every
prior stage's own precedent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.assets.models import Asset, AssetVersion, CreativeBrief


class CreativeBriefPublic(BaseModel):
    spec: dict[str, Any]


class AssetVersionPublic(BaseModel):
    storage_reference: str | None
    metadata: dict[str, Any]
    created_at: datetime


class AssetPublic(BaseModel):
    id: str
    kind: str
    status: str | None
    current_version: AssetVersionPublic | None


class AssetsForContentPieceResponse(BaseModel):
    creative_brief: CreativeBriefPublic | None
    assets: list[AssetPublic]


def creative_brief_to_public(creative_brief: CreativeBrief) -> CreativeBriefPublic:
    return CreativeBriefPublic(spec=creative_brief.spec)


def asset_version_to_public(version: AssetVersion) -> AssetVersionPublic:
    return AssetVersionPublic(
        storage_reference=version.storage_reference, metadata=version.metadata_, created_at=version.created_at
    )


def asset_to_public(asset: Asset, *, current_version: AssetVersion | None) -> AssetPublic:
    return AssetPublic(
        id=asset.public_id,
        kind=asset.kind,
        status=asset.status,
        current_version=asset_version_to_public(current_version) if current_version is not None else None,
    )
