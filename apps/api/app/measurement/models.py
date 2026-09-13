"""Measurement bounded context — BACKEND-11.

Persists exactly the entities the BACKEND-11 Governance Freeze authorizes
(Phase 1 + Phase 1B): ``MetricEntry`` (+ its ``MetricValue`` children),
``PerformanceObservation``, ``PerformanceSignal``, ``AnalysisResult``, and
three pure association tables connecting them. Performance Snapshot,
Measurement Cycle persistence/projection, Learning Candidate, Strategic
Recommendation Candidate, and any Metric/Analysis Approval concept are all
explicitly deferred — nothing below implements, references, or invents any
of them.

**Cardinality (Phase 1B §E/§F/§G), frozen:**
``MetricEntry <-> PerformanceObservation`` and ``PerformanceObservation <->
PerformanceSignal`` are both textually supported as multi-input
relationships ("one or more Metric Entries"; "a pattern... **across**
Observations") — each gets its own pure association table
(``observation_metric_entries``, ``signal_observations``), never a JSON
array of ids. ``PerformanceSignal -> AnalysisResult`` is different: BACKEND-01
says "AGENT-07's structured interpretation of **one or more Signals**,"
a many-Signals-to-one-Analysis-Result shape — but per the Governance
Freeze this is *still* represented as an association table
(``analysis_result_signals``), not a plain FK on ``PerformanceSignal``,
specifically **because Performance Signal is immutable**: setting an
`analysis_result_id` column on an existing Signal row after the fact
would be a mutation of an immutable row, which Phase 1B's own atomicity
analysis flagged as a real tension. The association table resolves that
tension by construction — Signal is never written to again after creation.

**Tenancy (frozen):** direct ``workspace_id`` on all four core entities
(``MetricEntry``, ``PerformanceObservation``, ``PerformanceSignal``,
``AnalysisResult``), each with a composite ownership FK to
``campaigns(id, workspace_id)`` (reusing the existing
``uq_campaigns_id_workspace_id`` candidate key — no change to
``campaigns``), and each declaring its own ``UniqueConstraint(id,
workspace_id)`` candidate key purely so the association tables can declare
composite, tenant-safe FKs against both sides of every edge — the same
"database itself cannot connect parents from different workspaces"
discipline already used throughout BACKEND-08/09/10. ``MetricValue`` is
via-parent only (no own ``workspace_id``) — a supporting child, not a
public domain entity.

**Provenance (frozen, Phase 1B §S):** ``campaign_id`` only, on all four
core entities. No ``campaign_run_id``/``stage_execution_id``/``agent_id``
anywhere in this module — Measurement is a continuous, campaign-scoped
accumulation process, not a single discrete per-run specialist artifact,
and BACKEND-01 states no run/stage provenance for any of these four
entities.

**Source vocabulary (frozen, Phase 1B §L):** ``MANUAL``, ``IMPORTED``,
``PLATFORM`` only. ``DERIVED`` is explicitly excluded — BACKEND-01's own
text says derived metrics become Performance Observations, never Metric
Entries.

**Immutability (frozen):** every entity in this module — ``MetricEntry``,
``MetricValue``, ``PerformanceObservation``, ``PerformanceSignal``,
``AnalysisResult``, and all three association tables — is write-once.
Nothing here has a ``status``, ``version``, ``archived_at``, or update
path of any kind. A Metric Entry "correction" is a brand-new row sharing
the same logical ``(campaign_id, period_start, period_end, channel)``
grouping, never a mutation of a prior row (Phase 1B §M) — deliberately, no
``UNIQUE(workspace_id, campaign_id, period_start, period_end, channel)``
constraint exists, since that would make corrections structurally
impossible. The only uniqueness constraint on ``MetricEntry`` is the
idempotency key ``UNIQUE(workspace_id, client_request_id)``, which exists
purely to make a retried POST/PUT a no-op, never to limit how many
logical corrections may exist for a period+channel.

PERSISTING A METRIC ENTRY != ANALYSIS COMPLETE. RECORDING AN OBSERVATION/
SIGNAL/ANALYSIS RESULT NEVER MUTATES ITS SOURCE ROWS. No chain-of-thought,
hidden reasoning, or internal agent identifier exists anywhere in this
module.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, UUIDPrimaryKeyMixin

_CHANNEL_MAX_LENGTH = 100
_METRIC_NAME_MAX_LENGTH = 100
_CLIENT_REQUEST_ID_MAX_LENGTH = 100
# Wide enough for both large counts (impressions) and currency values with
# cents — not a canonical requirement, a plain, generous numeric bound.
_METRIC_VALUE_PRECISION = 20
_METRIC_VALUE_SCALE = 4


class MetricSource(str, enum.Enum):
    """Exact BACKEND-11 Governance Freeze vocabulary (Phase 1B §L) — three
    values only. ``DERIVED`` is deliberately excluded: BACKEND-01's own
    text (`docs/backend/BACKEND-01-ARCHITECTURE.md` §6) states that
    "derived" metrics become Performance Observations, never Metric
    Entries — including it here would contradict that directly."""

    MANUAL = "MANUAL"
    IMPORTED = "IMPORTED"
    PLATFORM = "PLATFORM"


class MetricEntry(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "metric_entries"
    __table_args__ = (
        CheckConstraint("period_end >= period_start", name="period_end_after_start"),
        # Idempotency only — never a limit on how many logical corrections
        # may exist for a period+channel (Phase 1B §M). A retried POST/PUT
        # with the same client_request_id must be a no-op, not a new row.
        UniqueConstraint("workspace_id", "client_request_id", name="uq_metric_entries_workspace_client_request_id"),
        ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_metric_entries_campaign_workspace",
        ),
        # BACKEND-11 (explicitly authorized, additive-only): a candidate
        # key purely so the observation_metric_entries association table
        # can declare a composite, tenant-safe FK — the same pattern
        # already established in BACKEND-08/09/10.
        UniqueConstraint("id", "workspace_id", name="uq_metric_entries_id_workspace_id"),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    channel: Mapped[str] = mapped_column(String(_CHANNEL_MAX_LENGTH))
    source: Mapped[MetricSource] = mapped_column(Enum(MetricSource, name="metric_source", native_enum=True))
    client_request_id: Mapped[str] = mapped_column(String(_CLIENT_REQUEST_ID_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MetricValue(Base, UUIDPrimaryKeyMixin):
    """A supporting child of ``MetricEntry`` — not a public domain entity
    (no ``public_id``, no own ``workspace_id``, no API, no independent
    ``AuditEvent``). Exists so the open-ended, extensible set of metric
    names (impressions, clicks, ...) never requires a schema migration to
    grow — BACKEND-01 names no fixed metric vocabulary anywhere (Phase 1B
    §I), unlike Content Piece's own explicit field list."""

    __tablename__ = "metric_values"

    metric_entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("metric_entries.id"), index=True)
    metric_name: Mapped[str] = mapped_column(String(_METRIC_NAME_MAX_LENGTH))
    value: Mapped[Decimal] = mapped_column(Numeric(_METRIC_VALUE_PRECISION, _METRIC_VALUE_SCALE))


class PerformanceObservation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "performance_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_performance_observations_campaign_workspace",
        ),
        UniqueConstraint("id", "workspace_id", name="uq_performance_observations_id_workspace_id"),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    # BACKEND-01: "a normalized fact derived from one or more Metric
    # Entries (e.g., CTR for a period)" — no canonical field name is
    # given beyond the worked example; metric_name/value mirror
    # MetricValue's own shape for consistency, not because BACKEND-01
    # itemizes them.
    metric_name: Mapped[str] = mapped_column(String(_METRIC_NAME_MAX_LENGTH))
    value: Mapped[Decimal] = mapped_column(Numeric(_METRIC_VALUE_PRECISION, _METRIC_VALUE_SCALE))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PerformanceSignal(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "performance_signals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_performance_signals_campaign_workspace",
        ),
        UniqueConstraint("id", "workspace_id", name="uq_performance_signals_id_workspace_id"),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    # No canonical signal_type/severity vocabulary is named anywhere
    # (Phase 1B §Q) — a plain narrative field only, the minimal generic
    # shape BACKEND-01 actually supports.
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalysisResult(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "analysis_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_analysis_results_campaign_workspace",
        ),
        UniqueConstraint("id", "workspace_id", name="uq_analysis_results_id_workspace_id"),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    # "AGENT-07's structured interpretation" — a product-safe narrative
    # summary only, never a reasoning trace. No campaign_run_id/
    # stage_execution_id/agent_id (Phase 1B §S — Measurement is a
    # continuous, campaign-scoped process, not a single per-run artifact).
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ObservationMetricEntry(Base, UUIDPrimaryKeyMixin):
    """Pure relational association — no public_id, no status, no
    independent lifecycle. Records which Metric Entries fed a given
    Observation (BACKEND-01: "derived from one or more Metric Entries")."""

    __tablename__ = "observation_metric_entries"
    __table_args__ = (
        UniqueConstraint("observation_id", "metric_entry_id", name="uq_observation_metric_entries_pair"),
        ForeignKeyConstraint(
            ["observation_id", "workspace_id"],
            ["performance_observations.id", "performance_observations.workspace_id"],
            name="fk_observation_metric_entries_observation_workspace",
        ),
        ForeignKeyConstraint(
            ["metric_entry_id", "workspace_id"],
            ["metric_entries.id", "metric_entries.workspace_id"],
            name="fk_observation_metric_entries_metric_entry_workspace",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    observation_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    metric_entry_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above


class SignalObservation(Base, UUIDPrimaryKeyMixin):
    """Pure relational association. Records which Observations fed a
    given Signal (BACKEND-01: "a detected pattern... across
    Observations")."""

    __tablename__ = "signal_observations"
    __table_args__ = (
        UniqueConstraint("signal_id", "observation_id", name="uq_signal_observations_pair"),
        ForeignKeyConstraint(
            ["signal_id", "workspace_id"],
            ["performance_signals.id", "performance_signals.workspace_id"],
            name="fk_signal_observations_signal_workspace",
        ),
        ForeignKeyConstraint(
            ["observation_id", "workspace_id"],
            ["performance_observations.id", "performance_observations.workspace_id"],
            name="fk_signal_observations_observation_workspace",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    signal_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    observation_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above


class AnalysisResultSignal(Base, UUIDPrimaryKeyMixin):
    """Pure relational association. Records which Signals a given
    Analysis Result interpreted (BACKEND-01: "structured interpretation
    of one or more Signals"). Deliberately an association table, not a
    plain FK on ``PerformanceSignal`` — Signal is immutable, and writing
    an ``analysis_result_id`` onto an existing Signal row after the fact
    would mutate it."""

    __tablename__ = "analysis_result_signals"
    __table_args__ = (
        UniqueConstraint("analysis_result_id", "signal_id", name="uq_analysis_result_signals_pair"),
        ForeignKeyConstraint(
            ["analysis_result_id", "workspace_id"],
            ["analysis_results.id", "analysis_results.workspace_id"],
            name="fk_analysis_result_signals_analysis_result_workspace",
        ),
        ForeignKeyConstraint(
            ["signal_id", "workspace_id"],
            ["performance_signals.id", "performance_signals.workspace_id"],
            name="fk_analysis_result_signals_signal_workspace",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    analysis_result_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    signal_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above


# --- Measurement Analysis Run — MVP-11B (MVP-11A/-R1/-R2/-R3 Governance) --
#
# The deterministic analysis pipeline (MetricEntry -> Observation -> Signal
# -> AnalysisResult) is a NEW caller of the four frozen entities above, not
# a redesign of them. Per MVP-11A-R2's own repair: PerformanceObservation,
# PerformanceSignal, AnalysisResult, and MetricEntry are NEVER modified —
# no pipeline-specific column is added to any of them, and their existing
# generic capabilities (an Observation spanning multiple MetricEntries, a
# Signal spanning arbitrary Observations, an AnalysisResult spanning
# arbitrary Signals, all creatable with zero run of any kind) remain fully
# valid and untouched. Every pipeline-specific concept below lives entirely
# in new, purely-additive tables.
#
# MeasurementAnalysisRun is the only entity in this module that is NOT
# write-once/immutable in the strict sense the four core entities are — it
# has a real, intentionally small state machine (RUNNING -> COMPLETED |
# FAILED) because it represents a bounded, singular pipeline execution, not
# a piece of analytical evidence. Nothing here ever mutates a
# PerformanceObservation/PerformanceSignal/AnalysisResult/MetricEntry row.


class MeasurementAnalysisRunStatus(str, enum.Enum):
    """MVP-11A-R3 §14: the smallest truthful state machine — no PENDING
    (the pipeline is fully synchronous, so there is no observable gap
    between "row created" and "processing begins"), no PARTIAL/RETRYABLE/
    SUPERSEDED (a FAILED run's residual valid evidence is a property of
    the data, discoverable via the derivation/usage tables below, never a
    property the run's own status needs to encode)."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MeasurementAnalysisRun(Base, UUIDPrimaryKeyMixin):
    """One bounded execution of the deterministic analysis pipeline for a
    campaign. Idempotent by construction, exactly like MetricEntry: a
    retried request sharing the same ``(workspace_id, client_request_id)``
    must return the existing run rather than executing again (MVP-11A-R1
    §6/§22) — same-key replay is purely observational, even against a
    FAILED run; trying again always means a NEW run with a NEW key."""

    __tablename__ = "measurement_analysis_runs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "client_request_id", name="uq_measurement_analysis_runs_workspace_client_request_id"
        ),
        ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_measurement_analysis_runs_campaign_workspace",
        ),
        UniqueConstraint("id", "workspace_id", name="uq_measurement_analysis_runs_id_workspace_id"),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    client_request_id: Mapped[str] = mapped_column(String(_CLIENT_REQUEST_ID_MAX_LENGTH))
    status: Mapped[MeasurementAnalysisRunStatus] = mapped_column(
        Enum(MeasurementAnalysisRunStatus, name="measurement_analysis_run_status", native_enum=True),
        default=MeasurementAnalysisRunStatus.RUNNING,
        server_default=MeasurementAnalysisRunStatus.RUNNING.value,
    )
    # Public-safe only (MVP-11A-R3 §Z) — never raw exception/SQL/driver
    # text. NULL while RUNNING or COMPLETED.
    failure_reason: Mapped[str | None] = mapped_column(String(500), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class MeasurementAnalysisRunMetricEntry(Base, UUIDPrimaryKeyMixin):
    """The run's frozen input snapshot (MVP-11A-R2 §8/§13): every current
    MetricEntry selected the moment this run began, including ones that
    ultimately produce no Signal. Written once, in full, as part of the
    run's own start checkpoint (MVP-11A-R3 §13/§17) — never touched again,
    even after a later correction changes what counts as "current" going
    forward. This is what lets "what did this run evaluate" remain
    answerable permanently, without recomputing is_current retroactively."""

    __tablename__ = "measurement_analysis_run_metric_entries"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id", "metric_entry_id", name="uq_measurement_analysis_run_metric_entries_pair"
        ),
        ForeignKeyConstraint(
            ["analysis_run_id", "workspace_id"],
            ["measurement_analysis_runs.id", "measurement_analysis_runs.workspace_id"],
            name="fk_measurement_analysis_run_metric_entries_run_workspace",
        ),
        ForeignKeyConstraint(
            ["metric_entry_id", "workspace_id"],
            ["metric_entries.id", "metric_entries.workspace_id"],
            name="fk_measurement_analysis_run_metric_entries_entry_workspace",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    metric_entry_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above


class MeasurementObservationDerivation(Base, UUIDPrimaryKeyMixin):
    """Pipeline-specific identity + creator provenance for a
    PerformanceObservation, living entirely outside that frozen, generic
    table (MVP-11A-R2 Option B — chosen specifically so
    PerformanceObservation never needs a NOT NULL pipeline column, which
    would have broken its already-tested multi-MetricEntry generic
    capability). A generic/manually-created Observation simply has no row
    here at all — that absence, not a NULL value, is what distinguishes
    it from a pipeline-derived one.

    ``UNIQUE(source_metric_entry_id, metric_name)`` is the pipeline's
    natural-identity deduplication key (MVP-11A-R1 §H/§10, MVP-11A-R2 §I):
    a second analysis run deriving the same (entry, metric_name) pair
    reuses the existing Observation rather than creating a duplicate,
    found race-safely via insert-then-catch-IntegrityError, exactly like
    MetricEntry's own idempotency (MVP-11A-R3 §I/§22)."""

    __tablename__ = "measurement_observation_derivations"
    __table_args__ = (
        UniqueConstraint("observation_id", name="uq_measurement_observation_derivations_observation_id"),
        UniqueConstraint(
            "source_metric_entry_id", "metric_name", name="uq_measurement_observation_derivations_identity"
        ),
        ForeignKeyConstraint(
            ["observation_id", "workspace_id"],
            ["performance_observations.id", "performance_observations.workspace_id"],
            name="fk_measurement_observation_derivations_observation_workspace",
        ),
        ForeignKeyConstraint(
            ["source_metric_entry_id", "workspace_id"],
            ["metric_entries.id", "metric_entries.workspace_id"],
            name="fk_measurement_observation_derivations_entry_workspace",
        ),
        ForeignKeyConstraint(
            ["creator_analysis_run_id", "workspace_id"],
            ["measurement_analysis_runs.id", "measurement_analysis_runs.workspace_id"],
            name="fk_measurement_observation_derivations_run_workspace",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    observation_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    source_metric_entry_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    metric_name: Mapped[str] = mapped_column(String(_METRIC_NAME_MAX_LENGTH))
    creator_analysis_run_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above


class MeasurementSignalDerivation(Base, UUIDPrimaryKeyMixin):
    """Pipeline-specific identity + creator provenance for a
    PerformanceSignal, mirroring MeasurementObservationDerivation exactly
    (MVP-11A-R2 §J). ``UNIQUE(current_observation_id,
    prior_observation_id)`` is the Signal's natural-identity dedup key —
    the exact comparison pair a Signal represents. The CHECK constraint
    prevents a degenerate self-comparison (an Observation can never be its
    own baseline)."""

    __tablename__ = "measurement_signal_derivations"
    __table_args__ = (
        UniqueConstraint("signal_id", name="uq_measurement_signal_derivations_signal_id"),
        UniqueConstraint(
            "current_observation_id", "prior_observation_id", name="uq_measurement_signal_derivations_identity"
        ),
        CheckConstraint(
            "current_observation_id <> prior_observation_id",
            name="ck_measurement_signal_derivations_distinct_observations",
        ),
        ForeignKeyConstraint(
            ["signal_id", "workspace_id"],
            ["performance_signals.id", "performance_signals.workspace_id"],
            name="fk_measurement_signal_derivations_signal_workspace",
        ),
        ForeignKeyConstraint(
            ["current_observation_id", "workspace_id"],
            ["performance_observations.id", "performance_observations.workspace_id"],
            name="fk_measurement_signal_derivations_current_obs_workspace",
        ),
        ForeignKeyConstraint(
            ["prior_observation_id", "workspace_id"],
            ["performance_observations.id", "performance_observations.workspace_id"],
            name="fk_measurement_signal_derivations_prior_obs_workspace",
        ),
        ForeignKeyConstraint(
            ["creator_analysis_run_id", "workspace_id"],
            ["measurement_analysis_runs.id", "measurement_analysis_runs.workspace_id"],
            name="fk_measurement_signal_derivations_run_workspace",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    signal_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    current_observation_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    prior_observation_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    creator_analysis_run_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above


class MeasurementAnalysisRunObservationUsage(Base, UUIDPrimaryKeyMixin):
    """Consumer provenance (MVP-11A-R2 §7/§L, distinct from creator
    provenance above): records every Observation a run actually evaluated,
    whether it created that Observation or reused one created by an
    earlier run. Never inferred from current state or from timing — a
    stored fact, so a run's exact evidence scope remains reconstructable
    permanently, even after later corrections change what "current" means
    going forward."""

    __tablename__ = "measurement_analysis_run_observation_usages"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id", "observation_id", name="uq_measurement_analysis_run_observation_usages_pair"
        ),
        ForeignKeyConstraint(
            ["analysis_run_id", "workspace_id"],
            ["measurement_analysis_runs.id", "measurement_analysis_runs.workspace_id"],
            name="fk_measurement_analysis_run_observation_usages_run_workspace",
        ),
        ForeignKeyConstraint(
            ["observation_id", "workspace_id"],
            ["performance_observations.id", "performance_observations.workspace_id"],
            name="fk_measurement_analysis_run_observation_usages_obs_workspace",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    observation_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above


class MeasurementAnalysisRunSignalUsage(Base, UUIDPrimaryKeyMixin):
    """Consumer provenance for Signal, mirroring
    MeasurementAnalysisRunObservationUsage exactly (MVP-11A-R2 §M)."""

    __tablename__ = "measurement_analysis_run_signal_usages"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "signal_id", name="uq_measurement_analysis_run_signal_usages_pair"),
        ForeignKeyConstraint(
            ["analysis_run_id", "workspace_id"],
            ["measurement_analysis_runs.id", "measurement_analysis_runs.workspace_id"],
            name="fk_measurement_analysis_run_signal_usages_run_workspace",
        ),
        ForeignKeyConstraint(
            ["signal_id", "workspace_id"],
            ["performance_signals.id", "performance_signals.workspace_id"],
            name="fk_measurement_analysis_run_signal_usages_signal_workspace",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    signal_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above


class MeasurementAnalysisRunResult(Base, UUIDPrimaryKeyMixin):
    """AnalysisResult ownership (MVP-11A-R2 §N, Option 16.B): unlike
    Observation/Signal, an AnalysisResult has no natural content identity
    to deduplicate against — it IS, by definition, the interpretation one
    specific run produced. ``UNIQUE(analysis_run_id)`` enforces "at most
    one AnalysisResult per run"; ``UNIQUE(analysis_result_id)`` enforces
    "each pipeline-owned AnalysisResult belongs to exactly one run". A
    generic/manually-created AnalysisResult simply has no row here."""

    __tablename__ = "measurement_analysis_run_results"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", name="uq_measurement_analysis_run_results_run_id"),
        UniqueConstraint("analysis_result_id", name="uq_measurement_analysis_run_results_result_id"),
        ForeignKeyConstraint(
            ["analysis_run_id", "workspace_id"],
            ["measurement_analysis_runs.id", "measurement_analysis_runs.workspace_id"],
            name="fk_measurement_analysis_run_results_run_workspace",
        ),
        ForeignKeyConstraint(
            ["analysis_result_id", "workspace_id"],
            ["analysis_results.id", "analysis_results.workspace_id"],
            name="fk_measurement_analysis_run_results_result_workspace",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    analysis_result_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
