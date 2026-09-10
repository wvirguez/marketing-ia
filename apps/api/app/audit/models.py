"""Audit Event — BACKEND-06, implementing the entity already named and
prefixed by BACKEND-01 (`docs/backend/BACKEND-01-ARCHITECTURE.md` §8,
`docs/backend/BACKEND-01-DOMAIN-MODEL.md` §3: prefix ``AUDT``). Kept as
its own top-level module per the proposed module boundary (§9 of the
architecture doc lists ``audit/`` separately from ``orchestration/``),
so a future module beyond orchestration (content, paid_media, ...) can
append to the same event stream without importing ``orchestration``.

Append-only, by construction, not just by convention: this model
deliberately does NOT use ``TimestampMixin`` — there is no ``updated_at``
column at all, so there is no ``onupdate`` trigger a future change could
ever accidentally rely on. CURRENT STATE != STATE TRANSITION HISTORY
(BACKEND-06 §21): the event log is evidence, not mutable operational
state, and no router in this codebase exposes ``PATCH``/``DELETE`` for
it (see ``app/audit/repository.py`` — no update/delete method exists
either).

AUDIT EVENT != GOVERNANCE DECISION (BACKEND-01 §8): recording an event
here never itself grants authority over anything. A Human Decision
Response is the authority-bearing record; an Audit Event referencing it
is only a fact log entry about it having happened.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, UUIDPrimaryKeyMixin


class ActorType(str, enum.Enum):
    """BACKEND-06 §22. ``AGENT`` exists only for forward compatibility —
    no BACKEND-06 code path ever emits an event with this actor type,
    since no agent executes anything yet."""

    USER = "USER"
    SYSTEM = "SYSTEM"
    AGENT = "AGENT"


class AuditEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_events"

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    # BACKEND-12 Governance Freeze Repair: nullable — a genuinely
    # workspace-independent event (e.g. "user.preferences.updated", which
    # has no workspace context at all per BACKEND-01's own User
    # Preferences catalog entry) must never fabricate a workspace_id to
    # satisfy this column. Every workspace-scoped event (every one
    # written before BACKEND-12) still always supplies a real value here
    # — this repair only widens what the column *permits*, it does not
    # change what any existing write path *populates*.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workspaces.id"), default=None, index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("campaigns.id"), default=None, index=True)
    campaign_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaign_runs.id"), default=None, index=True
    )
    stage_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("run_stage_executions.id"), default=None, index=True
    )
    # BACKEND-06R: without this, a decision-related event
    # (event_type="orchestration.decision.opened"/"...resolved") could
    # not be attributed to a specific HumanDecisionRequest — a run may
    # legitimately raise more than one decision, sequentially, over its
    # lifetime, and the events for two such requests would otherwise be
    # indistinguishable except by assuming strict chronological pairing
    # (inference), which the append-only traceability invariant
    # forbids. Every other entity this table can reference
    # (campaign/run/stage) already had its own FK; this was the one gap.
    decision_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("human_decision_requests.id"), default=None, index=True
    )
    # BACKEND-07 §13: same reasoning as decision_request_id above — a
    # research.report.recorded/audience.profile.recorded event must
    # identify its exact ResearchReport/AudienceProfile, never only be
    # inferable from campaign_id/campaign_run_id/stage_execution_id
    # (which two different report versions for the same campaign/run
    # would share).
    research_report_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_reports.id"), default=None, index=True
    )
    audience_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("audience_profiles.id"), default=None, index=True
    )
    # BACKEND-08 §17: same reasoning as research_report_id/audience_profile_id
    # above — a strategy.recorded/strategy.hypothesis.recorded/
    # strategy.hypothesis.status_changed/strategy.experiment.recorded event
    # must identify its exact Strategy/Hypothesis/Experiment, never only be
    # inferable from campaign_id/campaign_run_id/stage_execution_id.
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("strategies.id"), default=None, index=True)
    hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("hypotheses.id"), default=None, index=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("experiments.id"), default=None, index=True)
    # BACKEND-09 §14: same reasoning as strategy_id/hypothesis_id/
    # experiment_id above — a planning.plan.recorded/planning.plan_item.
    # recorded event must identify its exact ContentPlan/PlanItem, never
    # only be inferable from campaign_id/campaign_run_id/stage_execution_id
    # (which two different plan versions for the same campaign/run would
    # share) or from sequence/ordering (which never uniquely identifies a
    # row across concurrent writes).
    content_plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("content_plans.id"), default=None, index=True)
    plan_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plan_items.id"), default=None, index=True)
    # BACKEND-10 §32: same reasoning as content_plan_id/plan_item_id
    # above — a content.brief.recorded/content.piece.recorded/content.
    # version.recorded/content.piece.status_changed/content.approval.*
    # event must identify its exact Content Brief/Piece/Version/Approval,
    # never only be inferable from an "assume the latest row" positional
    # guess. No content_revision_request_id exists — that entity is
    # deferred (BACKEND-10 §36).
    content_brief_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("content_briefs.id"), default=None, index=True)
    content_piece_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("content_pieces.id"), default=None, index=True)
    content_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_versions.id"), default=None, index=True
    )
    content_approval_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_approvals.id"), default=None, index=True
    )
    # BACKEND-11 §16: same reasoning as content_brief_id/content_piece_id
    # above — a measurement.metric_entry.recorded/measurement.observation.
    # recorded/measurement.signal.recorded/measurement.analysis_result.
    # recorded event must identify its exact row. MetricValue and the
    # three pure association tables do not receive independent AuditEvent
    # FKs — they are supporting/relational rows, not domain entities.
    metric_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("metric_entries.id"), default=None, index=True)
    performance_observation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("performance_observations.id"), default=None, index=True
    )
    performance_signal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("performance_signals.id"), default=None, index=True
    )
    analysis_result_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_results.id"), default=None, index=True
    )
    # BACKEND-13 §18: same reasoning as content_brief_id/content_piece_id
    # above — an assets.creative_brief.recorded/assets.asset.recorded/
    # assets.asset_version.recorded/assets.asset.archived event must
    # identify its exact row, never only be inferable from
    # content_piece_id/content_brief_id (which the CreativeBrief already
    # is 0..1 for, but Asset/AssetVersion are 1:N).
    creative_brief_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("creative_briefs.id"), default=None, index=True
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id"), default=None, index=True)
    asset_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("asset_versions.id"), default=None, index=True
    )
    # BACKEND-14 §18: same reasoning as creative_brief_id/asset_id above —
    # a learning.candidate.recorded/learning.candidate.status_changed/
    # learning.recommendation.recorded/learning.recommendation.decided
    # event must identify its exact row. Both nullable, single-column,
    # matching every other AuditEvent subject FK exactly — no composite
    # audit FK, no AuditEvent-specific candidate key (Governance Freeze §25).
    learning_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("learning_candidates.id"), default=None, index=True
    )
    # Explicit, shortened FK name: the naming convention's own derived
    # name ("fk_audit_events_strategic_recommendation_candidate_id_
    # strategic_recommendation_candidates") exceeds PostgreSQL's
    # 63-character identifier limit (verified empirically: 89 chars).
    strategic_recommendation_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategic_recommendation_candidates.id", name="fk_audit_events_strategic_recommendation_candidate_id_src"),
        default=None,
        index=True,
    )
    # BACKEND-15 §21: same reasoning as learning_candidate_id/
    # strategic_recommendation_candidate_id above — a
    # tracking.plan.recorded/tracking.plan.status_changed/
    # tracking.requirement.recorded/tracking.requirement.status_changed
    # event must identify its exact row(s). Both nullable, single-column,
    # matching every other AuditEvent subject FK exactly — no composite
    # audit FK, no AuditEvent-specific candidate key (Governance Freeze §22).
    # Both auto-derived FK/index names fit within PostgreSQL's 63-character
    # limit (verified empirically: 47/61/32/39 chars) — no explicit
    # shortening needed, unlike BACKEND-14's own strategic_recommendation_
    # candidate_id column.
    tracking_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tracking_plans.id"), default=None, index=True
    )
    tracking_requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tracking_requirements.id"), default=None, index=True
    )
    # A short, stable, dot-separated tag (e.g. "orchestration.run.transitioned")
    # — see app/orchestration/service.py for the fixed set BACKEND-06 emits.
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    previous_state: Mapped[str | None] = mapped_column(String(100), default=None)
    new_state: Mapped[str | None] = mapped_column(String(100), default=None)
    actor_type: Mapped[ActorType] = mapped_column(Enum(ActorType, name="audit_actor_type", native_enum=True))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    # Correlation only (X-Request-ID) — never treated as an auth signal
    # here either, matching app/core/middleware.py's own documented rule.
    request_id: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
