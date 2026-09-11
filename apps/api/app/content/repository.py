"""Data access for Content Brief / Piece / Version / Approval. No
repository here calls ``session.commit()`` — see
``app/persistence/session.py`` and ``app/content/service.py`` for the
transaction-ownership boundary.

Every ``create``/``create_many`` method takes the parent domain object
(``PlanItem``, ``ContentPlan``, ``ContentBrief``, ``ContentPiece``,
``ContentVersion``), never a raw ``workspace_id``/``plan_item_id``/
``content_brief_id``/... parameter — the same "no independent parameter, no
possibility of drift" pattern established in
``app/strategy/repository.py``/``app/planning/repository.py``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.models import ContentApproval, ContentBrief, ContentPiece, ContentVersion
from app.core.ids import generate_public_id
from app.planning.models import ContentPlan, PlanItem


class ContentBriefRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, plan_item: PlanItem, content_plan: ContentPlan, brief: str) -> ContentBrief:
        row = ContentBrief(
            public_id=generate_public_id("CBRF"),
            workspace_id=content_plan.workspace_id,
            plan_item_id=plan_item.id,
            content_plan_id=content_plan.id,
            brief=brief,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_public_id(self, public_id: str) -> ContentBrief | None:
        return self.session.execute(
            select(ContentBrief).where(ContentBrief.public_id == public_id)
        ).scalar_one_or_none()

    def get_for_plan_item(self, plan_item_id: uuid.UUID) -> ContentBrief | None:
        return self.session.execute(
            select(ContentBrief).where(ContentBrief.plan_item_id == plan_item_id)
        ).scalar_one_or_none()


class ContentPieceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        content_brief: ContentBrief,
        format: str,
        objective: str,
        funnel_stage: str,
        cta: str,
        channel: str,
    ) -> ContentPiece:
        row = ContentPiece(
            public_id=generate_public_id("CNT"),
            workspace_id=content_brief.workspace_id,
            content_brief_id=content_brief.id,
            format=format,
            objective=objective,
            funnel_stage=funnel_stage,
            cta=cta,
            channel=channel,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_public_id(self, public_id: str, *, for_update: bool = False) -> ContentPiece | None:
        query = select(ContentPiece).where(ContentPiece.public_id == public_id)
        if for_update:
            query = query.with_for_update()
        return self.session.execute(query).scalar_one_or_none()

    def get_by_id(self, content_piece_id: uuid.UUID, *, for_update: bool = False) -> ContentPiece | None:
        if for_update:
            query = select(ContentPiece).where(ContentPiece.id == content_piece_id).with_for_update()
            return self.session.execute(query).scalar_one_or_none()
        return self.session.get(ContentPiece, content_piece_id)

    def get_for_brief(self, content_brief_id: uuid.UUID) -> ContentPiece | None:
        """MVP-05E idempotency check: no DB-level uniqueness exists on
        ``content_brief_id`` (a Brief could structurally have multiple
        Pieces), so the deterministic bootstrap's own "one Piece per Brief"
        policy is enforced here at the application level, not the schema
        level."""
        return self.session.execute(
            select(ContentPiece).where(ContentPiece.content_brief_id == content_brief_id)
        ).scalar_one_or_none()

    def get_for_campaign_by_public_id(self, *, campaign_id: uuid.UUID, public_id: str) -> ContentPiece | None:
        """Non-leaky resource-scope lookup (mirrors
        ``CampaignAccessService.get_authorized_run``): a Content Piece that
        does not exist at all, or that exists but belongs to a different
        Campaign, is indistinguishable — both return ``None`` here, and
        the router turns that into the same ``ForbiddenError`` either way."""
        return self.session.execute(
            select(ContentPiece)
            .join(ContentBrief, ContentPiece.content_brief_id == ContentBrief.id)
            .join(ContentPlan, ContentBrief.content_plan_id == ContentPlan.id)
            .where(ContentPlan.campaign_id == campaign_id, ContentPiece.public_id == public_id)
        ).scalar_one_or_none()

    def list_for_campaign(self, campaign_id: uuid.UUID) -> list[ContentPiece]:
        """Traverses ContentPiece -> ContentBrief -> ContentPlan (via the
        tenant-safety-anchor ``content_plan_id`` already on ContentBrief,
        never via PlanItem) to find every Content Piece belonging to any
        Content Plan version of this Campaign — Content Pieces are not
        themselves versioned, so this is not restricted to only the
        current Content Plan version."""
        return list(
            self.session.execute(
                select(ContentPiece)
                .join(ContentBrief, ContentPiece.content_brief_id == ContentBrief.id)
                .join(ContentPlan, ContentBrief.content_plan_id == ContentPlan.id)
                .where(ContentPlan.campaign_id == campaign_id)
                .order_by(ContentPiece.created_at.asc(), ContentPiece.id.asc())
            )
            .scalars()
            .all()
        )


class ContentVersionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self, *, content_piece: ContentPiece, payload: dict, created_by_user_id: uuid.UUID | None
    ) -> ContentVersion:
        row = ContentVersion(
            public_id=generate_public_id("CNV"),
            content_piece_id=content_piece.id,
            payload=payload,
            created_by_user_id=created_by_user_id,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_latest_for_piece(self, content_piece_id: uuid.UUID) -> ContentVersion | None:
        """Deterministic "current version" selection (Phase 1B §I): no
        ordinal column exists — ordered by ``created_at DESC, id DESC``."""
        return self.session.execute(
            select(ContentVersion)
            .where(ContentVersion.content_piece_id == content_piece_id)
            .order_by(ContentVersion.created_at.desc(), ContentVersion.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    def get_by_public_id(self, public_id: str) -> ContentVersion | None:
        return self.session.execute(
            select(ContentVersion).where(ContentVersion.public_id == public_id)
        ).scalar_one_or_none()

    def get_by_id(self, content_version_id: uuid.UUID) -> ContentVersion | None:
        return self.session.get(ContentVersion, content_version_id)


class ContentApprovalRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, content_version: ContentVersion) -> ContentApproval:
        row = ContentApproval(public_id=generate_public_id("APR"), content_version_id=content_version.id)
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_public_id(self, public_id: str, *, for_update: bool = False) -> ContentApproval | None:
        query = select(ContentApproval).where(ContentApproval.public_id == public_id)
        if for_update:
            query = query.with_for_update()
        return self.session.execute(query).scalar_one_or_none()
