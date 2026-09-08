"""Research bounded context — BACKEND-07.

Persists exactly the four entities BACKEND-01 canonically defines for the
``research`` module (`docs/backend/BACKEND-01-ARCHITECTURE.md` §1:
"Research Report, Source Reference, VOC Evidence, Audience Profile") and
no others — no ``ResearchFinding``, no ``AudienceInsight``: BACKEND-01's
own entity catalog treats the Report's own ``summary`` as the finding
itself, and VOC Evidence belongs directly to Audience Profile (the ER
diagram in `BACKEND-01-DOMAIN-MODEL.md` §4 draws no separate Insight node).

Ownership matches that same ER diagram exactly: both ``ResearchReport``
and ``AudienceProfile`` are Campaign-owned (siblings of Orchestration
Run under Campaign, not children of it), with ``campaign_run_id`` and
``stage_execution_id`` carried as *required provenance* — which run and
which stage-execution instance this version was produced for — never as
the primary ownership relationship. A new CampaignRun therefore never
overwrites an earlier run's evidence: each version is its own row,
addressable by exactly which run produced it.

Immutability: none of these four models use ``TimestampMixin`` — only a
single ``created_at`` is declared directly, with no ``updated_at``
column at all. This is deliberate, not an oversight (matching
``app/audit/models.py``'s own reasoning for the same omission): BACKEND-01
describes every one of these four entities as immutable/not-versioned-
in-place, and giving a domain-mutability-implying ``onupdate`` trigger to
an entity that must never be edited would misrepresent the contract in
the schema itself, not just in application code.

PERSISTED RESEARCH != VALIDATED TRUTH. AUDIENCE EVIDENCE != STRATEGIC
DECISION. Neither this module nor any field on these models implies an
AI agent produced the row, a Gate accepted it, or it is strategically
approved — there is no ``status``, ``confidence``, ``approval``, or
``agent_id`` field anywhere below, by design (BACKEND-07 §5/§7/§18).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, UUIDPrimaryKeyMixin

_EXCERPT_MAX_LENGTH = 2000
_TITLE_MAX_LENGTH = 300
_LOCATOR_MAX_LENGTH = 2000
_PUBLISHER_MAX_LENGTH = 200
_VOC_QUOTE_MAX_LENGTH = 4000


class SourceType(str, enum.Enum):
    """Neutral, descriptive-only classification (BACKEND-07 §6) — never a
    reliability/truth/confidence signal. Reused as-is for
    ``VOCEvidence.source_type`` (same vocabulary, same neutrality)."""

    ARTICLE = "ARTICLE"
    REPORT = "REPORT"
    FORUM = "FORUM"
    SOCIAL = "SOCIAL"
    SURVEY = "SURVEY"
    OTHER = "OTHER"


class ResearchReport(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "research_reports"
    __table_args__ = (
        UniqueConstraint("campaign_id", "version", name="uq_research_reports_campaign_version"),
        # Ownership: workspace-safe by construction — Postgres rejects a
        # Report whose workspace diverges from its Campaign's, the same
        # composite-FK pattern already established for CampaignRun
        # (BACKEND-06 §6), reusing the existing
        # ``uq_campaigns_id_workspace_id`` candidate key with no
        # Campaign schema change.
        ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_research_reports_campaign_workspace",
        ),
        # Provenance: which run, workspace-safe the same way — reuses the
        # new BACKEND-07-authorized ``CampaignRun(id, workspace_id)``
        # candidate key (see app/campaigns/models.py). This does not by
        # itself prove the run belongs to *this* campaign (a composite FK
        # can only check one workspace_id equality, not campaign_id
        # equality) — that cross-check is service-layer
        # (`app/research/service.py`), not database-layer, by design
        # (BACKEND-07 §10: a plain/composite FK is not enough by itself
        # to prove semantic provenance correctness).
        ForeignKeyConstraint(
            ["campaign_run_id", "workspace_id"],
            ["campaign_runs.id", "campaign_runs.workspace_id"],
            name="fk_research_reports_campaign_run_workspace",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    campaign_run_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    # Plain FK only — the semantic check ("this stage_execution belongs
    # to this exact campaign_run and is the RESEARCH stage") is proven in
    # the service layer, not assumed from the FK's mere existence
    # (BACKEND-07 §10).
    stage_execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("run_stage_executions.id"), index=True)
    version: Mapped[int] = mapped_column()
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResearchSource(Base, UUIDPrimaryKeyMixin):
    """Belongs to exactly one ``ResearchReport`` — no direct
    ``workspace_id``/``campaign_id`` column, tenant reached only by
    traversal through the parent Report, matching BACKEND-01's own words
    for Source Reference: "Workspace (via Report)" (the same "via
    parent" pattern already used by ``CampaignBrief``)."""

    __tablename__ = "research_sources"

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    research_report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_reports.id"), index=True)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType, name="source_type", native_enum=True))
    title: Mapped[str] = mapped_column(String(_TITLE_MAX_LENGTH))
    # Never assumed to be a URL — a citation, reference, or plain
    # description is equally valid (BACKEND-07 §6).
    locator: Mapped[str | None] = mapped_column(String(_LOCATOR_MAX_LENGTH), default=None)
    publisher: Mapped[str | None] = mapped_column(String(_PUBLISHER_MAX_LENGTH), default=None)
    # A bounded excerpt — evidence context, never a full page/thread dump
    # (BACKEND-07 §6/§19).
    excerpt: Mapped[str | None] = mapped_column(String(_EXCERPT_MAX_LENGTH), default=None)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AudienceProfile(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audience_profiles"
    __table_args__ = (
        UniqueConstraint("campaign_id", "version", name="uq_audience_profiles_campaign_version"),
        ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_audience_profiles_campaign_workspace",
        ),
        ForeignKeyConstraint(
            ["campaign_run_id", "workspace_id"],
            ["campaign_runs.id", "campaign_runs.workspace_id"],
            name="fk_audience_profiles_campaign_run_workspace",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(index=True)
    campaign_run_id: Mapped[uuid.UUID] = mapped_column(index=True)
    stage_execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("run_stage_executions.id"), index=True)
    version: Mapped[int] = mapped_column()
    # AUDIENCE EVIDENCE != STRATEGY (BACKEND-07 §18): this is a
    # structured audience definition only — no `is_target`,
    # `primary_target`, `approved_target`, or `positioning` field exists
    # anywhere on this model, deliberately, since those are Strategy
    # bounded-context concepts BACKEND-01 assigns elsewhere.
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VOCEvidence(Base, UUIDPrimaryKeyMixin):
    """Belongs to exactly one ``AudienceProfile`` (BACKEND-01 ER diagram:
    ``Audience Profile ─── * VOC Evidence`` — no edge to Research
    Source is drawn there, so none is added here either, per the
    BACKEND-07 gate's explicit instruction)."""

    __tablename__ = "voc_evidence"
    __table_args__ = (
        # RAW VOC != PARAPHRASE != nothing: a row recording neither is
        # meaningless. The service/schema boundary additionally
        # normalizes blank/whitespace-only strings to NULL before this
        # constraint is ever evaluated (see app/research/service.py), so
        # it cannot be bypassed with an empty-but-non-null string.
        CheckConstraint(
            "verbatim_quote IS NOT NULL OR paraphrase IS NOT NULL",
            name="quote_or_paraphrase",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    audience_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audience_profiles.id"), index=True)
    # Deliberately distinct columns — a paraphrase must never silently
    # overwrite or be conflated with the original wording (BACKEND-07 §8).
    verbatim_quote: Mapped[str | None] = mapped_column(String(_VOC_QUOTE_MAX_LENGTH), default=None)
    paraphrase: Mapped[str | None] = mapped_column(String(_VOC_QUOTE_MAX_LENGTH), default=None)
    source_type: Mapped[SourceType | None] = mapped_column(
        Enum(SourceType, name="source_type", native_enum=True, create_type=False), default=None
    )
    source_locator: Mapped[str | None] = mapped_column(String(_LOCATOR_MAX_LENGTH), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
