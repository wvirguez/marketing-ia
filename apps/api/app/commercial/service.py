"""Commercial persistence — MVP-27, frozen by MVP-27A/-R1/-R2.

CAMPAIGN-SCOPED RESOURCE INTEGRITY: every mutation here takes an
already-loaded, already-authorized ``Campaign`` — never a bare
``workspace_id`` — because active Workspace membership alone never
proves a given CommercialObjective/Offer belongs to the Campaign named in
the URL. Lookups are campaign-scoped via
``CommercialObjectiveRepository``/``OfferRepository``'s own
``get_for_campaign_by_public_id``, the same non-leaky discipline every
other bounded context in this codebase already uses.

SUPERSESSION (MVP-27A-R1 §E/§G, MVP-27A-R2 §K): one atomic domain
command — lock the specific original row ``FOR UPDATE`` (never a
Campaign-wide lock; different Objectives/Offers are independently
supersedable), re-read fresh (``populate_existing=True``), raise a
dedicated conflict if already superseded, create a fresh replacement
using the *original's own* ``campaign``/``workspace_id`` (never a
client-supplied value — this is also what makes cross-Campaign/
cross-Workspace replacement structurally impossible), mark the original,
record both audit events, and commit exactly once. Self-supersession and
cycles are structurally impossible by construction: the replacement is
always a brand-new row, never a client-referenced existing one.

AUDIT (MVP-27A-R2 §I/§J): the authoritative replacement link is each
row's own ``superseded_by_commercial_objective_id``/``superseded_by_
offer_id`` — permanent, FK-enforced, since rows are immutable and never
deleted. ``new_state`` additionally carries a human-legible counterpart
public_id reference (``f"supersedes:{...}"``/``f"superseded_by:{...}"``),
mirroring the existing ``f"v{version}"`` convention already used by
Strategy/Research/Planning for their own versioned successors — never
the authoritative link.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign
from app.commercial.models import CommercialObjective, Offer
from app.commercial.repository import CommercialObjectiveRepository, OfferRepository
from app.core.api_errors import CommercialObjectiveAlreadySupersededError, ForbiddenError, OfferAlreadySupersededError

EVENT_OBJECTIVE_RECORDED = "commercial.objective.recorded"
EVENT_OBJECTIVE_SUPERSEDED = "commercial.objective.superseded"
EVENT_OFFER_RECORDED = "commercial.offer.recorded"
EVENT_OFFER_SUPERSEDED = "commercial.offer.superseded"


class CommercialService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.objectives = CommercialObjectiveRepository(session)
        self.offers = OfferRepository(session)
        self.events = AuditEventRepository(session)

    # --- CommercialObjective ---------------------------------------------

    def record_commercial_objective(
        self,
        *,
        campaign: Campaign,
        statement: str,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> CommercialObjective:
        """Creates one additional, independent, current CommercialObjective
        — never replaces an existing one, never designates itself
        PRIMARY (0..N simultaneously-current, MVP-27A-R1 §C). No lock: an
        independent create shares no target row with any other create."""
        objective = self.objectives.create(campaign=campaign, statement=statement)
        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=objective.workspace_id,
            event_type=EVENT_OBJECTIVE_RECORDED,
            actor_type=actor_type,
            commercial_objective_id=objective.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return objective

    def supersede_commercial_objective(
        self,
        *,
        campaign: Campaign,
        objective_public_id: str,
        statement: str,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> CommercialObjective:
        """Atomic supersession (MVP-27A-R1 §E/§G): locks the specific
        original Objective row, creates a fresh replacement in the same
        transaction, marks the original, records both audit events, one
        commit. Returns the replacement."""
        original = self.objectives.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=objective_public_id, for_update=True
        )
        if original is None:
            raise ForbiddenError()
        if original.superseded_at is not None:
            raise CommercialObjectiveAlreadySupersededError()

        replacement = self.objectives.create(campaign=campaign, statement=statement)
        original.superseded_at = datetime.now(timezone.utc)
        original.superseded_by_commercial_objective_id = replacement.id

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=replacement.workspace_id,
            event_type=EVENT_OBJECTIVE_RECORDED,
            actor_type=actor_type,
            commercial_objective_id=replacement.id,
            new_state=f"supersedes:{original.public_id}",
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.events.record(
            workspace_id=original.workspace_id,
            event_type=EVENT_OBJECTIVE_SUPERSEDED,
            actor_type=actor_type,
            commercial_objective_id=original.id,
            new_state=f"superseded_by:{replacement.public_id}",
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return replacement

    def list_objectives_for_campaign(self, campaign_id: uuid.UUID) -> list[CommercialObjective]:
        return self.objectives.list_for_campaign(campaign_id)

    # --- Offer -------------------------------------------------------------

    def record_offer(
        self,
        *,
        campaign: Campaign,
        statement: str,
        price: Decimal | None = None,
        currency: str | None = None,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> Offer:
        """Creates one additional, independent, current Offer — never
        replaces an existing one (0..N simultaneously-current). No lock:
        an independent create shares no target row with any other
        create. Money-pair/non-negative validity is enforced at the
        request-schema layer (``app/commercial/schemas.py``) plus the
        database CheckConstraints (defense-in-depth) — never re-validated
        here."""
        offer = self.offers.create(campaign=campaign, statement=statement, price=price, currency=currency)
        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=offer.workspace_id,
            event_type=EVENT_OFFER_RECORDED,
            actor_type=actor_type,
            offer_id=offer.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return offer

    def supersede_offer(
        self,
        *,
        campaign: Campaign,
        offer_public_id: str,
        statement: str,
        price: Decimal | None = None,
        currency: str | None = None,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> Offer:
        """Atomic supersession — identical shape to
        ``supersede_commercial_objective`` above. Returns the
        replacement."""
        original = self.offers.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=offer_public_id, for_update=True
        )
        if original is None:
            raise ForbiddenError()
        if original.superseded_at is not None:
            raise OfferAlreadySupersededError()

        replacement = self.offers.create(campaign=campaign, statement=statement, price=price, currency=currency)
        original.superseded_at = datetime.now(timezone.utc)
        original.superseded_by_offer_id = replacement.id

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=replacement.workspace_id,
            event_type=EVENT_OFFER_RECORDED,
            actor_type=actor_type,
            offer_id=replacement.id,
            new_state=f"supersedes:{original.public_id}",
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.events.record(
            workspace_id=original.workspace_id,
            event_type=EVENT_OFFER_SUPERSEDED,
            actor_type=actor_type,
            offer_id=original.id,
            new_state=f"superseded_by:{replacement.public_id}",
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return replacement

    def list_offers_for_campaign(self, campaign_id: uuid.UUID) -> list[Offer]:
        return self.offers.list_for_campaign(campaign_id)
