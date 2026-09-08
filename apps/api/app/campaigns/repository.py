"""Data access for Campaign / Campaign Brief / Campaign Run.

No repository here calls ``session.commit()`` — see
``app/persistence/session.py`` and ``app/campaigns/service.py`` for the
transaction-ownership boundary.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.campaigns.models import Campaign, CampaignBrief, CampaignRun, CampaignRunStatus, CampaignStatus
from app.core.ids import generate_public_id


class CampaignRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, workspace_id: uuid.UUID, name: str) -> Campaign:
        campaign = Campaign(
            public_id=generate_public_id("CMP"),
            workspace_id=workspace_id,
            name=name,
            status=CampaignStatus.SUBMITTED,
        )
        self.session.add(campaign)
        self.session.flush()
        return campaign

    def get_by_public_id(self, public_id: str) -> Campaign | None:
        return self.session.execute(select(Campaign).where(Campaign.public_id == public_id)).scalar_one_or_none()

    def list_for_workspace(
        self, *, workspace_id: uuid.UUID, include_archived: bool, limit: int, offset: int
    ) -> tuple[list[Campaign], int]:
        conditions = [Campaign.workspace_id == workspace_id]
        if not include_archived:
            conditions.append(Campaign.archived_at.is_(None))

        total = self.session.execute(
            select(func.count()).select_from(Campaign).where(*conditions)
        ).scalar_one()

        items = (
            self.session.execute(
                select(Campaign)
                .where(*conditions)
                .order_by(Campaign.created_at.desc(), Campaign.id.desc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
        return list(items), total


class CampaignBriefRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        campaign_id: uuid.UUID,
        version: int,
        prompt: str,
        product_type: str | None,
        price: str | None,
        audience: str | None,
        budget: str | None,
        channel: str | None,
    ) -> CampaignBrief:
        brief = CampaignBrief(
            public_id=generate_public_id("CBR"),
            campaign_id=campaign_id,
            version=version,
            prompt=prompt,
            product_type=product_type,
            price=price,
            audience=audience,
            budget=budget,
            channel=channel,
        )
        self.session.add(brief)
        self.session.flush()
        return brief


class CampaignRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, campaign: Campaign, run_number: int) -> CampaignRun:
        """Takes the parent ``Campaign`` object, not a raw
        ``workspace_id`` parameter — BACKEND-06 §6's tenancy invariant
        (``CampaignRun.workspace_id`` must equal ``Campaign.workspace_id``)
        is enforced here structurally: there is no independent
        ``workspace_id`` input a caller could pass inconsistently, since
        it is always derived from the campaign being run. The database's
        own composite foreign key (see ``app/campaigns/models.py``)
        enforces the same invariant independently, as defense in depth.
        """
        run = CampaignRun(
            public_id=generate_public_id("RUN"),
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.id,
            run_number=run_number,
            status=CampaignRunStatus.CREATED,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def get_by_public_id(self, public_id: str, *, for_update: bool = False) -> CampaignRun | None:
        query = select(CampaignRun).where(CampaignRun.public_id == public_id)
        if for_update:
            query = query.with_for_update()
        return self.session.execute(query).scalar_one_or_none()

    def list_for_campaign(self, *, campaign_id: uuid.UUID, limit: int, offset: int) -> tuple[list[CampaignRun], int]:
        total = self.session.execute(
            select(func.count()).select_from(CampaignRun).where(CampaignRun.campaign_id == campaign_id)
        ).scalar_one()

        items = (
            self.session.execute(
                select(CampaignRun)
                .where(CampaignRun.campaign_id == campaign_id)
                .order_by(CampaignRun.run_number.asc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
        return list(items), total
