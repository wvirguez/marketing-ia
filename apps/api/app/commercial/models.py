"""Commercial bounded context — MVP-27, frozen by MVP-27A/-R1/-R2.

Persists ``CommercialObjective`` ("what the Campaign is commercially
trying to achieve") and ``Offer`` ("what is commercially being offered")
— the smallest repository-consistent governance surface the MVP-27A
design discovery concluded is truthfully representable today. Strategic
Decision, Strategic Approval, Strategy mutation, Strategic Maturity,
CommercialOutcome (success/failure declaration, conversion, revenue
evidence, ROI, attribution, causality, demand proof), Product catalog,
promotion/discount/payment/checkout/CRM concepts, Experiment/Variant, and
any Content/Tracking/Measurement/Learning change are all explicitly
deferred — nothing below implements, references, or invents any of them.

**Cardinality (MVP-27A-R1 §C, reversing MVP-27A's own original "exactly
one current" position):** ``Campaign 1 -> 0..N`` simultaneously-current
rows for both entities — no PRIMARY designation, no ``version``/
``MAX(version)`` mechanism. "Current" = ``superseded_at IS NULL``.

**Lifecycle:** immutable from INSERT except one, one-shot disposition —
supersession. Superseding is a single atomic domain command (locks the
specific original row, creates a fresh replacement in the same
transaction, marks the original) — never an in-place edit of any other
field. Self-supersession and cycles are structurally impossible by
construction (the replacement is always a freshly-generated row, never a
client-referenced existing one); re-supersession of an already-superseded
row is the one invariant requiring an explicit service-level check
(``app/commercial/service.py``).

**Tenancy:** direct, un-qualified ``workspace_id`` on both, plus a
composite tenant-safe FK to ``campaigns (id, workspace_id)`` — the exact
``app/tracking/models.py::TrackingPlan`` pattern, one level shallower
(0..N here, not 0..1). Archived Campaigns do not block creation or
supersession here (MVP-27A-R1 §L): no other domain in this codebase
gates child-entity creation on ``Campaign.archived_at`` either, and
introducing one only for Commercial would be an inconsistent special
case, not a repository semantic.

**CommercialObjective has no target field, no version, no status enum**
(MVP-27A-R1 §J, reversing MVP-27A's own ``target_value`` field) — a
quantitative aim, if any, belongs directly in the narrative ``statement``.

**Offer money (MVP-27A-R2 §C/§D/§F):** ``price``/``currency`` are a
both-null-or-both-non-null pair — NULL+NULL is unknown/undetermined
pricing (including variable pricing, described in ``statement`` text if
at all); zero+currency is a genuinely free, known price. ``price`` uses
dedicated, currency-owned ``Numeric(12, 4)`` constants — deliberately
distinct from ``app/measurement/models.py``'s own generic
``_METRIC_VALUE_PRECISION``/``_METRIC_VALUE_SCALE`` (a different semantic
category), and scale 4 (not 2) so no real currency's actual minor-unit
granularity (0, 2, or 3 fractional digits) is silently truncated — this
is a storage-safety choice only, never a claim about any specific
currency's official convention. Negative price is invalid (no discount/
refund/rebate/credit/cashback/promotion concept exists in this domain).
``currency`` is a bounded ``String(3)``; the alphabetic-only
``^[A-Z]{3}$`` shape is enforced at the request-schema layer only
(``app/commercial/schemas.py``), mirroring the existing
``_AI_PREFERENCE_PATTERN`` precedent in ``app/workspaces/schemas.py`` —
no ISO-4217 catalog is claimed or built.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, UUIDPrimaryKeyMixin

# Currency-owned money precision (MVP-27A-R2 §C) — never imported from
# app/measurement/models.py's own, semantically distinct, generic
# _METRIC_VALUE_PRECISION/_METRIC_VALUE_SCALE.
_OFFER_PRICE_PRECISION = 12
_OFFER_PRICE_SCALE = 4
_OFFER_CURRENCY_LENGTH = 3


class CommercialObjective(Base, UUIDPrimaryKeyMixin):
    """A bounded, human-authored statement of a desired business result
    for a Campaign — distinct from a content/marketing objective
    (``ContentPiece.objective``/``PlanItem.objective``), a KPI, a
    CommercialOutcome, a Strategic Decision, a budget, or an Offer.

    0..N simultaneously current per Campaign (MVP-27A-R1 §C) — a Campaign
    may truthfully pursue more than one commercial objective at once
    (e.g. "generate qualified leads" and "generate revenue"
    simultaneously), with no ranking between them. No ``target_value``
    (MVP-27A-R1 §J): a quantitative aim belongs in ``statement`` itself.
    """

    __tablename__ = "commercial_objectives"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_commercial_objectives_campaign_workspace",
        ),
        CheckConstraint(
            "(superseded_at IS NULL AND superseded_by_commercial_objective_id IS NULL) OR "
            "(superseded_at IS NOT NULL AND superseded_by_commercial_objective_id IS NOT NULL)",
            name="disposition_complete",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    statement: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Disposition (MVP-27A-R1 §E/§F): both null (current) or both set
    # (superseded, exactly once) — never one without the other.
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Explicit, shortened FK name: the naming convention's own derived
    # name ("fk_commercial_objectives_superseded_by_commercial_objective_
    # id_commercial_objectives") exceeds PostgreSQL's 63-character
    # identifier limit (verified empirically: 84 chars) — the same repair
    # BACKEND-14 already applied to strategic_recommendation_candidate_id.
    superseded_by_commercial_objective_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("commercial_objectives.id", name="fk_commercial_objectives_superseded_by_id"), default=None
    )


class Offer(Base, UUIDPrimaryKeyMixin):
    """A bounded, human-authored statement of what is commercially being
    offered for a Campaign, with an optional known price. 0..N
    simultaneously current per Campaign (MVP-27A/-R1) — multiple current
    Offers are valid (e.g. two different product/price combinations being
    tested at once).

    Never conflated with ``CampaignBrief.price``/``product_type``
    (MVP-27A-R1 §I): those remain legacy/intake, non-canonical, free-text
    fields with no synchronization, derivation, or fallback to/from this
    table in either direction.
    """

    __tablename__ = "offers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_offers_campaign_workspace",
        ),
        CheckConstraint(
            "(superseded_at IS NULL AND superseded_by_offer_id IS NULL) OR "
            "(superseded_at IS NOT NULL AND superseded_by_offer_id IS NOT NULL)",
            name="disposition_complete",
        ),
        CheckConstraint(
            "(price IS NULL AND currency IS NULL) OR (price IS NOT NULL AND currency IS NOT NULL)",
            name="price_currency_pair",
        ),
        CheckConstraint("price IS NULL OR price >= 0", name="price_non_negative"),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    statement: Mapped[str] = mapped_column(Text)
    # Free = 0 (a real, known price) + a real currency code. Unknown/
    # undetermined/variable pricing = NULL + NULL (MVP-27A-R2 §E).
    price: Mapped[Decimal | None] = mapped_column(Numeric(_OFFER_PRICE_PRECISION, _OFFER_PRICE_SCALE), default=None)
    currency: Mapped[str | None] = mapped_column(String(_OFFER_CURRENCY_LENGTH), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    superseded_by_offer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("offers.id"), default=None)
