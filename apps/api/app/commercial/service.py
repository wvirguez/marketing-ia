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

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign
from app.commercial.models import CommercialObjective, CommercialOutcome, Offer
from app.commercial.repository import CommercialObjectiveRepository, CommercialOutcomeRepository, OfferRepository
from app.core.api_errors import (
    CommercialObjectiveAlreadySupersededError,
    CommercialOutcomeCorrectionTargetStaleError,
    ForbiddenError,
    IdempotencyKeyConflictError,
    OfferAlreadySupersededError,
)

EVENT_OBJECTIVE_RECORDED = "commercial.objective.recorded"
EVENT_OBJECTIVE_SUPERSEDED = "commercial.objective.superseded"
EVENT_OFFER_RECORDED = "commercial.offer.recorded"
EVENT_OFFER_SUPERSEDED = "commercial.offer.superseded"
EVENT_OUTCOME_RECORDED = "commercial.outcome.recorded"
EVENT_OUTCOME_CORRECTED = "commercial.outcome.corrected"


class CommercialService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.objectives = CommercialObjectiveRepository(session)
        self.offers = OfferRepository(session)
        self.outcomes = CommercialOutcomeRepository(session)
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

    # --- CommercialOutcome (MVP-36, frozen by MVP-36A/-R1) ------------------

    def _existing_outcome_create_match(
        self,
        *,
        existing: CommercialOutcome,
        campaign: Campaign,
        content_distribution_id: uuid.UUID | None,
        outcome_type: str,
        quantity: int | None,
        monetary_value: Decimal | None,
        currency: str | None,
        occurred_at: datetime,
        external_reference: str | None,
    ) -> CommercialOutcome | None:
        """MVP-36A-R1 §3: an exact logical replay of a CREATE request —
        same campaign, same optional Distribution, same outcome fields.
        Comparison is on canonical persisted values (Decimal-vs-Decimal
        for monetary_value, timestamptz-vs-timestamptz for occurred_at),
        never raw request serialization — mirrors
        ``MeasurementService._existing_evidence_create_match`` exactly."""
        if existing.campaign_id != campaign.id:
            return None
        if existing.content_distribution_id != content_distribution_id:
            return None
        if existing.outcome_type != outcome_type or existing.quantity != quantity:
            return None
        if existing.monetary_value != monetary_value or existing.currency != currency:
            return None
        if existing.occurred_at != occurred_at or existing.external_reference != external_reference:
            return None
        return existing

    def record_commercial_outcome(
        self,
        *,
        campaign: Campaign,
        content_distribution_id: uuid.UUID | None,
        outcome_type: str,
        quantity: int | None,
        monetary_value: Decimal | None,
        currency: str | None,
        occurred_at: datetime,
        external_reference: str | None,
        client_request_id: str,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> tuple[CommercialOutcome, bool]:
        """Returns ``(outcome, created)`` — ``created`` is ``False`` for
        both a fast-path idempotent replay and a race-lost-but-identical
        replay, ``True`` only for a genuinely new row. Raises
        ``IdempotencyKeyConflictError`` (409) if ``client_request_id`` was
        already used for a materially different request (MVP-36A-R1 §3/§4).
        USER-only (MVP-36A §M) — no SYSTEM producer exists for MVP-36."""
        existing = self.outcomes.get_by_workspace_and_request_id(
            workspace_id=campaign.workspace_id, client_request_id=client_request_id
        )
        if existing is not None:
            match = self._existing_outcome_create_match(
                existing=existing, campaign=campaign, content_distribution_id=content_distribution_id,
                outcome_type=outcome_type, quantity=quantity, monetary_value=monetary_value, currency=currency,
                occurred_at=occurred_at, external_reference=external_reference,
            )
            if match is None:
                raise IdempotencyKeyConflictError()
            return match, False

        try:
            outcome = self.outcomes.create(
                campaign=campaign, content_distribution_id=content_distribution_id, outcome_type=outcome_type,
                quantity=quantity, monetary_value=monetary_value, currency=currency, occurred_at=occurred_at,
                external_reference=external_reference, client_request_id=client_request_id,
            )
            self.events.record(
                workspace_id=campaign.workspace_id, event_type=EVENT_OUTCOME_RECORDED, actor_type=ActorType.USER,
                campaign_id=campaign.id, commercial_outcome_id=outcome.id, actor_user_id=actor_user_id,
                request_id=request_id,
            )
        except IntegrityError:
            # Lost the UNIQUE(workspace_id, client_request_id) race —
            # exactly one caller's insert can ever win under this key.
            self.session.rollback()
            existing = self.outcomes.get_by_workspace_and_request_id(
                workspace_id=campaign.workspace_id, client_request_id=client_request_id
            )
            if existing is None:
                raise
            match = self._existing_outcome_create_match(
                existing=existing, campaign=campaign, content_distribution_id=content_distribution_id,
                outcome_type=outcome_type, quantity=quantity, monetary_value=monetary_value, currency=currency,
                occurred_at=occurred_at, external_reference=external_reference,
            )
            if match is None:
                raise IdempotencyKeyConflictError() from None
            return match, False
        self.session.commit()
        return outcome, True

    def _existing_outcome_correction_match(
        self,
        *,
        existing: CommercialOutcome,
        campaign: Campaign,
        target_outcome_public_id: str,
        outcome_type: str,
        quantity: int | None,
        monetary_value: Decimal | None,
        currency: str | None,
        occurred_at: datetime,
        external_reference: str | None,
        correction_reason: str,
    ) -> CommercialOutcome | None:
        if existing.campaign_id != campaign.id or existing.supersedes_outcome_id is None:
            return None
        target = self.outcomes.get_by_id(existing.supersedes_outcome_id)
        if target is None or target.public_id != target_outcome_public_id:
            return None
        if existing.outcome_type != outcome_type or existing.quantity != quantity:
            return None
        if existing.monetary_value != monetary_value or existing.currency != currency:
            return None
        if existing.occurred_at != occurred_at or existing.external_reference != external_reference:
            return None
        if existing.correction_reason != correction_reason:
            return None
        return existing

    def correct_commercial_outcome(
        self,
        *,
        campaign: Campaign,
        target_outcome_public_id: str,
        outcome_type: str,
        quantity: int | None,
        monetary_value: Decimal | None,
        currency: str | None,
        occurred_at: datetime,
        external_reference: str | None,
        client_request_id: str,
        correction_reason: str,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> tuple[CommercialOutcome, bool]:
        """FULL-STATE correction (MVP-36A-R1 §9): the new row is the
        complete corrected claim, never a patch. ``content_distribution_id``
        is deliberately not a parameter here at all — it is always
        inherited unconditionally from ``target``, structurally impossible
        to change through a correction.

        MVP-36A-R1 §7/§10 mandatory ordering: idempotency resolution
        happens BEFORE the target is resolved/locked — an exact successful
        replay of a prior correction must return that correction's Outcome
        with ``created=False``, never a stale-target conflict, even if a
        LATER, different correction has since superseded the same target
        further. Mirrors
        ``MeasurementService.create_distribution_evidence_correction``
        exactly, one bounded context over."""
        existing = self.outcomes.get_by_workspace_and_request_id(
            workspace_id=campaign.workspace_id, client_request_id=client_request_id
        )
        if existing is not None:
            match = self._existing_outcome_correction_match(
                existing=existing, campaign=campaign, target_outcome_public_id=target_outcome_public_id,
                outcome_type=outcome_type, quantity=quantity, monetary_value=monetary_value, currency=currency,
                occurred_at=occurred_at, external_reference=external_reference, correction_reason=correction_reason,
            )
            if match is None:
                raise IdempotencyKeyConflictError()
            return match, False

        # No replay found — only now resolve and lock the target, verify
        # it is still the current leaf (TIP-ONLY, MVP-36A-R1 §8), and
        # create the successor.
        target = self.outcomes.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=target_outcome_public_id, for_update=True
        )
        if target is None:
            raise ForbiddenError()
        if self.outcomes.get_successor(target.id) is not None:
            raise CommercialOutcomeCorrectionTargetStaleError()

        try:
            outcome = self.outcomes.create(
                campaign=campaign, content_distribution_id=target.content_distribution_id,
                outcome_type=outcome_type, quantity=quantity, monetary_value=monetary_value, currency=currency,
                occurred_at=occurred_at, external_reference=external_reference, client_request_id=client_request_id,
                supersedes=target, correction_reason=correction_reason,
            )
            self.events.record(
                workspace_id=campaign.workspace_id, event_type=EVENT_OUTCOME_CORRECTED, actor_type=ActorType.USER,
                campaign_id=campaign.id, commercial_outcome_id=outcome.id, actor_user_id=actor_user_id,
                request_id=request_id,
            )
        except IntegrityError:
            self.session.rollback()
            existing = self.outcomes.get_by_workspace_and_request_id(
                workspace_id=campaign.workspace_id, client_request_id=client_request_id
            )
            if existing is not None:
                match = self._existing_outcome_correction_match(
                    existing=existing, campaign=campaign, target_outcome_public_id=target_outcome_public_id,
                    outcome_type=outcome_type, quantity=quantity, monetary_value=monetary_value, currency=currency,
                    occurred_at=occurred_at, external_reference=external_reference,
                    correction_reason=correction_reason,
                )
                if match is not None:
                    return match, False
                raise IdempotencyKeyConflictError() from None
            # Our own client_request_id was never persisted at all — this
            # was not a key collision, it was the single-successor race
            # (UNIQUE(supersedes_outcome_id)) against a different,
            # concurrent correction that already claimed this same leaf.
            raise CommercialOutcomeCorrectionTargetStaleError() from None
        self.session.commit()
        return outcome, True

    def get_outcome_successor_id(self, outcome_id: uuid.UUID) -> uuid.UUID | None:
        successor = self.outcomes.get_successor(outcome_id)
        return successor.id if successor is not None else None

    def list_commercial_outcomes_for_campaign(self, campaign_id: uuid.UUID) -> list[CommercialOutcome]:
        return self.outcomes.list_for_campaign(campaign_id)
