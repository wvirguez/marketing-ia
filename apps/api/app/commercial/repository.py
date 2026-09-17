"""Data access for CommercialObjective / Offer — MVP-27.

No repository method here calls ``session.commit()`` — see
``app/commercial/service.py`` for the transaction-ownership boundary.
Every ``create`` method takes the parent ``Campaign`` (never a raw
``workspace_id``/``campaign_id`` parameter), the same "no independent
parameter, no possibility of drift" pattern established throughout this
codebase (see ``app/tracking/repository.py``).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.campaigns.models import Campaign
from app.commercial.models import CommercialObjective, Offer
from app.core.ids import generate_public_id


class CommercialObjectiveRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, campaign: Campaign, statement: str) -> CommercialObjective:
        row = CommercialObjective(
            public_id=generate_public_id("OBJ"),
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.id,
            statement=statement,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_id(self, objective_id: uuid.UUID, *, for_update: bool = False) -> CommercialObjective | None:
        if for_update:
            query = (
                select(CommercialObjective)
                .where(CommercialObjective.id == objective_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            return self.session.execute(query).scalar_one_or_none()
        return self.session.get(CommercialObjective, objective_id)

    def get_for_campaign_by_public_id(
        self, *, campaign_id: uuid.UUID, public_id: str, for_update: bool = False
    ) -> CommercialObjective | None:
        """Non-leaky, campaign-scoped resource lookup (mirrors
        ``StrategicImplicationRepository.get_for_campaign_by_public_id``):
        an Objective that does not exist at all, that belongs to a
        different workspace, or that belongs to a different Campaign
        within the *same* workspace, is indistinguishable — all three
        return ``None`` here. ``for_update=True`` locks the specific
        target row, never the whole Campaign (MVP-27A-R1 §G)."""
        query = select(CommercialObjective).where(
            CommercialObjective.campaign_id == campaign_id, CommercialObjective.public_id == public_id
        )
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.execute(query).scalar_one_or_none()

    def list_for_campaign(self, campaign_id: uuid.UUID) -> list[CommercialObjective]:
        return list(
            self.session.execute(
                select(CommercialObjective)
                .where(CommercialObjective.campaign_id == campaign_id)
                .order_by(CommercialObjective.created_at.asc(), CommercialObjective.id.asc())
            )
            .scalars()
            .all()
        )


class OfferRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self, *, campaign: Campaign, statement: str, price: Decimal | None = None, currency: str | None = None
    ) -> Offer:
        row = Offer(
            public_id=generate_public_id("OFR"),
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.id,
            statement=statement,
            price=price,
            currency=currency,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_id(self, offer_id: uuid.UUID, *, for_update: bool = False) -> Offer | None:
        if for_update:
            query = select(Offer).where(Offer.id == offer_id).with_for_update().execution_options(populate_existing=True)
            return self.session.execute(query).scalar_one_or_none()
        return self.session.get(Offer, offer_id)

    def get_for_campaign_by_public_id(
        self, *, campaign_id: uuid.UUID, public_id: str, for_update: bool = False
    ) -> Offer | None:
        """Non-leaky, campaign-scoped resource lookup — identical
        discipline to ``CommercialObjectiveRepository`` above. Locks the
        specific target Offer row only, never the whole Campaign."""
        query = select(Offer).where(Offer.campaign_id == campaign_id, Offer.public_id == public_id)
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.execute(query).scalar_one_or_none()

    def list_for_campaign(self, campaign_id: uuid.UUID) -> list[Offer]:
        return list(
            self.session.execute(
                select(Offer).where(Offer.campaign_id == campaign_id).order_by(Offer.created_at.asc(), Offer.id.asc())
            )
            .scalars()
            .all()
        )
