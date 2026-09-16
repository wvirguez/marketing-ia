"""Public DTOs for the Assets surface. Never expose an internal UUID, a
raw ``workspace_id``, or a ``CreativeBrief``/``AssetVersion`` internal id
(neither has a ``public_id`` — see ``app/assets/models.py``).
``CreativeBrief`` exposes only its ``spec`` payload — never its ``id``,
``workspace_id``, or ``content_piece_id`` (BACKEND-13 Governance Freeze:
the frozen response contract names ``creative_brief: {"spec": ...} |
null`` alongside ``assets: [...]``).

No field here ever represents distribution readiness, content approval
authority, or a chain-of-thought/reasoning trace. Actor attribution lives
in the Audit Event trail, not this domain read model, matching every
prior stage's own precedent.

MVP-16B creation request DTOs (frozen, MVP-16A §8-10): ``status`` and
``metadata`` are deliberately never accepted from the client at creation
time — the service already defaults them, and no public mutation path for
either exists yet. ``storage_reference`` is an opaque external/storage
reference string only — it may contain a URL, but is never validated,
parsed, or dereferenced as one anywhere in this module.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.assets.models import Asset, AssetVersion, CreativeBrief

_KIND_MAX_LENGTH = 100
_STORAGE_REFERENCE_MAX_LENGTH = 2048


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


class CreateCreativeBriefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Arbitrary JSON object — no domain-specific keys are named anywhere
    # (app/assets/models.py::CreativeBrief.spec), so none are required here.
    spec: dict[str, Any]


class CreateAssetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=_KIND_MAX_LENGTH)
    storage_reference: str | None = Field(default=None, max_length=_STORAGE_REFERENCE_MAX_LENGTH)


class CreateAssetVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    storage_reference: str | None = Field(default=None, max_length=_STORAGE_REFERENCE_MAX_LENGTH)


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
