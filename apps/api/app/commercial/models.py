"""Commercial bounded context — MVP-27 (CommercialObjective/Offer, frozen
by MVP-27A/-R1/-R2), extended by MVP-36 (CommercialOutcome, frozen by
MVP-36A/-R1).

Persists ``CommercialObjective`` ("what the Campaign is commercially
trying to achieve"), ``Offer`` ("what is commercially being offered"),
and ``CommercialOutcome`` ("what commercial/business event actually
occurred") — the smallest repository-consistent governance surface each
stage's own design discovery concluded is truthfully representable.
Strategic Decision, Strategic Approval, Strategy mutation, Strategic
Maturity, success/failure declaration, ROI, attribution, causality, demand
proof, Product catalog, promotion/discount/payment/checkout/CRM concepts,
Experiment/Variant, and any Content/Tracking/Measurement/Learning
automation remain explicitly deferred — nothing below implements,
references, or invents any of them. CommercialOutcome itself is
deliberately NOT proof of CommercialObjective achievement, campaign
success, content effectiveness, or an Experiment result (MVP-36A §X/§Y) —
see ``CommercialOutcome``'s own docstring below for its full frozen
boundary set.

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

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, UUIDPrimaryKeyMixin

# Currency-owned money precision (MVP-27A-R2 §C) — never imported from
# app/measurement/models.py's own, semantically distinct, generic
# _METRIC_VALUE_PRECISION/_METRIC_VALUE_SCALE.
_OFFER_PRICE_PRECISION = 12
_OFFER_PRICE_SCALE = 4
_OFFER_CURRENCY_LENGTH = 3

# MVP-36A §J/§AD (frozen by MVP-36A-R1): CommercialOutcome's own
# currency-owned constants — deliberately distinct instances from Offer's
# above (never shared) and from app/measurement/models.py's own
# _METRIC_VALUE_PRECISION/_METRIC_VALUE_SCALE, matching this module's own
# established "no cross-domain money-precision sharing" discipline.
_OUTCOME_TYPE_MAX_LENGTH = 200
_OUTCOME_MONETARY_VALUE_PRECISION = 12
_OUTCOME_MONETARY_VALUE_SCALE = 4
_OUTCOME_CURRENCY_LENGTH = 3
_OUTCOME_EXTERNAL_REFERENCE_MAX_LENGTH = 2048
# Mirrors app/measurement/models.py's own _CLIENT_REQUEST_ID_MAX_LENGTH
# value exactly (100) — a local, domain-owned constant, not a cross-module
# import, matching this module's own money-precision discipline above.
_OUTCOME_CLIENT_REQUEST_ID_MAX_LENGTH = 100


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


class CommercialOutcome(Base, UUIDPrimaryKeyMixin):
    """A governed, human-reported record of one realized commercial/
    business EVENT (MVP-36A §I, frozen by MVP-36A-R1) — never an aggregate
    period rollup. Semantic parent is Campaign (mandatory); an optional
    ``content_distribution_id`` means ONLY that this outcome was
    observationally recorded in association with that Distribution —
    never that the Distribution caused, generated, or is responsible for
    it (MVP-36A §G, the Attribution Firewall). No ``experiment_id``,
    ``variant_id``, ``commercial_objective_id``, ``offer_id``,
    ``tracking_requirement_id``, or any attribution/causal/success/winner
    field exists here, or ever will under this frozen contract.

    Cardinality: Campaign 1 -> 0..N, ContentDistribution 0..1 -> 0..N
    (MVP-36A §E/§K) — no PRIMARY, no uniqueness on business fields.
    Identical business fields under two different ``client_request_id``
    values are two legitimately distinct events, never a duplicate error.

    Immutability: immutable from INSERT except one, TIP-ONLY correction
    chain (MVP-36A §O, MVP-36A-R1 §8) — independently justified, not
    copied from Offer/CommercialObjective's own supersession model, since
    a CommercialOutcome is a claim about one specific past real-world
    event (Evidence's own category), not a currently-held business
    position. A correction is a brand-new row whose own
    ``supersedes_outcome_id`` points backward at the row it replaces —
    the original is never mutated. "Current"/effective (no successor yet)
    is always derived at read time from the ``UNIQUE(supersedes_outcome_id)``
    relationship itself, never a stored flag (mirrors
    ``DistributionMetricEvidence``'s own precedent exactly).
    ``content_distribution_id`` is immutable through a correction chain —
    not a service-level "must match" check alone, but structurally never
    accepted as a correction-request field at all (MVP-36A-R1 §9); it is
    always inherited unconditionally from the row being corrected.

    Idempotency: ``UNIQUE(workspace_id, client_request_id)`` — the exact
    ``MetricEntry``/``MeasurementAnalysisRun`` shape. Every correction row
    requires its own distinct ``client_request_id`` in the same key space
    (MVP-36A-R1 §10) — a correction is never a mutation of its target's
    own creation key.
    """

    __tablename__ = "commercial_outcomes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_commercial_outcomes_campaign_workspace",
        ),
        # Optional observational provenance only (MVP-36A §G) — nullable,
        # never a mandatory causal link. Reuses content_distributions' own
        # uq_content_distributions_id_workspace_id candidate key (added by
        # MVP-19B), no new candidate key needed on that side.
        ForeignKeyConstraint(
            ["content_distribution_id", "workspace_id"],
            ["content_distributions.id", "content_distributions.workspace_id"],
            name="fk_commercial_outcomes_distribution_workspace",
        ),
        # Candidate key purely so the correction chain below can declare a
        # composite, tenant-and-campaign-safe self-FK — the same
        # "logically redundant but structurally required for a composite
        # FK" pattern DistributionMetricEvidence already establishes.
        UniqueConstraint(
            "id", "campaign_id", "workspace_id", name="uq_commercial_outcomes_id_campaign_workspace"
        ),
        # MVP-36A-R1 §8: a correction may target only a row in the SAME
        # Campaign and workspace — enforced here, not merely by service
        # discipline. content_distribution_id consistency across a chain
        # is enforced at the service layer only (MVP-36A-R1 §9): it is
        # structurally never a correction-request field, so no DB-level
        # equality check on it is needed here.
        ForeignKeyConstraint(
            ["supersedes_outcome_id", "campaign_id", "workspace_id"],
            ["commercial_outcomes.id", "commercial_outcomes.campaign_id", "commercial_outcomes.workspace_id"],
            name="fk_commercial_outcomes_supersedes_same_campaign",
        ),
        UniqueConstraint(
            "workspace_id", "client_request_id", name="uq_commercial_outcomes_workspace_client_request_id"
        ),
        # At most one immediate successor per row — the TIP-ONLY
        # correction-chain invariant's actual DB backstop (MVP-36A-R1 §8).
        UniqueConstraint("supersedes_outcome_id", name="uq_commercial_outcomes_supersedes_outcome_id"),
        CheckConstraint("quantity IS NULL OR quantity > 0", name="quantity_positive"),
        CheckConstraint(
            "(monetary_value IS NULL AND currency IS NULL) OR (monetary_value IS NOT NULL AND currency IS NOT NULL)",
            name="monetary_value_currency_pair",
        ),
        CheckConstraint("monetary_value IS NULL OR monetary_value >= 0", name="monetary_value_non_negative"),
        CheckConstraint(
            "(supersedes_outcome_id IS NULL AND correction_reason IS NULL) OR "
            "(supersedes_outcome_id IS NOT NULL AND correction_reason IS NOT NULL)",
            name="correction_reason_pairing",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    content_distribution_id: Mapped[uuid.UUID | None] = mapped_column(index=True, default=None)
    # EVENT semantics only (MVP-36A §I): one row = one realized business
    # event. No canonical taxonomy is named anywhere for this — a plain
    # bounded, open label, the same "do not invent an unnamed vocabulary"
    # discipline already applied to Experiment.status/TrackingRequirement.name.
    outcome_type: Mapped[str] = mapped_column(String(_OUTCOME_TYPE_MAX_LENGTH))
    # The count of units WITHIN this one event (e.g. 3 items in one
    # order) — never a period rollup of separate events (MVP-36A §I).
    quantity: Mapped[int | None] = mapped_column(Integer, default=None)
    # This one event's total monetary value — Decimal, never float.
    # NULL+NULL = unknown/undetermined; zero+currency = a real, known,
    # free outcome (mirrors Offer's own price/currency pairing exactly,
    # independently justified per MVP-36A-R1 §2.2, not auto-copied).
    monetary_value: Mapped[Decimal | None] = mapped_column(
        Numeric(_OUTCOME_MONETARY_VALUE_PRECISION, _OUTCOME_MONETARY_VALUE_SCALE), default=None
    )
    currency: Mapped[str | None] = mapped_column(String(_OUTCOME_CURRENCY_LENGTH), default=None)
    # The real-world business event time — client-supplied, distinct from
    # created_at, may precede it (MVP-36A §Q).
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    external_reference: Mapped[str | None] = mapped_column(String(_OUTCOME_EXTERNAL_REFERENCE_MAX_LENGTH), default=None)
    client_request_id: Mapped[str] = mapped_column(String(_OUTCOME_CLIENT_REQUEST_ID_MAX_LENGTH))
    supersedes_outcome_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    correction_reason: Mapped[str | None] = mapped_column(Text, default=None)
