"""Unit tests for the persistence foundation that do NOT require a real
database — pure Python/SQLAlchemy metadata introspection, always run.
"""

from __future__ import annotations

import uuid

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.persistence.base import Base, metadata
from app.persistence.probe import PersistenceProbe
from app.persistence.session import get_engine, is_configured
from app.persistence.testing import UnsafeTestDatabaseError, assert_safe_test_database_url

ALEMBIC_INI = "alembic.ini"


def test_naming_convention_covers_pk_fk_uq_ck_ix() -> None:
    assert metadata.naming_convention.keys() >= {"pk", "fk", "uq", "ck", "ix"}


def test_base_and_probe_share_one_metadata_object() -> None:
    # Alembic must import the project's own metadata, never a second one.
    assert PersistenceProbe.metadata is Base.metadata is metadata


def test_probe_table_is_marked_non_domain_by_name() -> None:
    assert PersistenceProbe.__tablename__.startswith("_infra")


def test_probe_has_uuid_primary_key() -> None:
    id_column = PersistenceProbe.__table__.columns["id"]
    assert id_column.primary_key is True
    assert id_column.type.python_type is uuid.UUID
    # Identify the default generator by name/module rather than object
    # identity or a direct no-arg call — SQLAlchemy wraps the callable
    # to accept an execution-context argument internally.
    generator = id_column.default.arg
    assert generator.__module__ == "uuid"
    assert generator.__name__ == "uuid4"


def test_probe_has_server_side_timestamp_columns() -> None:
    columns = PersistenceProbe.__table__.columns
    assert "created_at" in columns
    assert "updated_at" in columns
    assert columns["created_at"].server_default is not None
    assert columns["updated_at"].server_default is not None


def test_probe_primary_key_constraint_follows_naming_convention() -> None:
    pk = PersistenceProbe.__table__.primary_key
    assert pk.name == "pk__infra_persistence_probe"


def test_get_engine_before_configure_raises_clear_error() -> None:
    # This test must not permanently disable the database for tests that
    # run after it — it only checks the error path, never calls
    # configure_database, and asserts the pre-configuration state some
    # other test may have already set up is left completely alone.
    if is_configured():
        pytest.skip("Database already configured by another test in this session.")
    with pytest.raises(RuntimeError, match="not configured"):
        get_engine()


def test_alembic_config_loads_and_has_exactly_one_head() -> None:
    config = Config(ALEMBIC_INI)
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1


def test_no_agent_execution_or_content_domain_tables_exist_yet() -> None:
    """Scope check: identity/tenancy tables (BACKEND-04), Campaign/
    Campaign Brief/Campaign Run (BACKEND-05), the orchestration
    foundation — RunStageExecution, Human Decision Request/Response,
    Audit Event (BACKEND-06) — the Research/Audience persistence
    contracts — Research Report/Source, Audience Profile, VOC Evidence
    (BACKEND-07) — the Strategy persistence contracts — Strategy,
    Positioning, Hypothesis, Experiment (BACKEND-08) — the Planning
    persistence contracts — Content Plan, Plan Item (BACKEND-09) — the
    Content persistence contracts — Content Brief, Content Piece,
    Content Version, Content Approval (BACKEND-10) — the Measurement
    persistence contracts — Metric Entry, Metric Value, Performance
    Observation, Performance Signal, Analysis Result, and their three
    pure association tables (BACKEND-11) — the Settings persistence
    contracts — UserPreference, AIPreference, NotificationPreference
    (BACKEND-12) — the Assets persistence contracts — CreativeBrief,
    Asset, AssetVersion (BACKEND-13) — the Learning persistence
    contracts — LearningCandidate, StrategicRecommendationCandidate
    (BACKEND-14) — the Tracking persistence contracts —
    TrackingPlan, TrackingRequirement (BACKEND-15) — and the Measurement
    Analysis Run pipeline provenance contracts — MeasurementAnalysisRun,
    its input-snapshot association, the Observation/Signal derivation
    (natural-identity + creator) tables, the Observation/Signal usage
    (consumer-provenance) tables, and the AnalysisResult ownership table
    (MVP-11B) — and the Measurement -> Learning bridge's own provenance
    table, LearningDerivation (MVP-12B-A/-R1), which is purely additive
    and never adds a column to learning_candidates/analysis_results
    themselves — all exist, plus the MVP-24 ContentDistribution <->
    TrackingRequirement identity-only association table
    (content_distribution_tracking_requirements — MVP-24A-R1: no
    Requirement/Plan status snapshot column, no version column, no
    history table), plus the BACKEND-03 infrastructure probe, plus the
    MVP-27 Commercial persistence contracts — CommercialObjective, Offer
    — the MVP-28B Strategic Decision persistence contract (frozen
    MVP-28A/-R1/-R2 contract; owned by ``orchestration`` per MVP-28A-R2
    §D, not ``learning``/``strategy`` — both of those modules explicitly
    disclaim it in their own docstrings) — the MVP-29B Strategic
    Approval persistence contract (frozen MVP-29A contract; also owned by
    ``orchestration``, for the identical reason) — and the MVP-30B Governed
    Strategy Revision persistence contract (frozen MVP-30A/-30A-R1
    contract; the governance-provenance relation ``strategy_revisions`` is
    also owned by ``orchestration``, for the identical reason, while
    ``Strategy``/``Positioning`` themselves remain owned by ``strategy`` and
    gained only an additive ``origin`` column + a nullability relaxation on
    their own pre-existing bootstrap-provenance columns, never a new table
    of their own) — and the MVP-36 governed CommercialOutcome persistence
    contract (frozen MVP-36A/-R1 contract; owned by ``commercial``
    alongside CommercialObjective/Offer — EVENT semantics only, Campaign
    mandatory + ContentDistribution optional observational provenance,
    no experiment_id/variant_id/commercial_objective_id/offer_id/
    tracking_requirement_id/causal/success/winner column) — and the MVP-37
    governed Experiment Definition persistence contract (frozen
    MVP-37A/-37B; owned by ``strategy`` — one append-only, versioned
    declared-comparison table, no Variant/measurement/allocation/result/
    execution column) — and the MVP-38 governed Variant identity
    persistence contract (frozen MVP-38A/-38B; owned by ``strategy`` — one
    append-only identity table pinned to an immutable definition version;
    no role/allocation/exposure/measurement/result/execution column) — and
    the MVP-39 governed Measurement Contract persistence contract (frozen
    MVP-39A/-39B; owned by ``strategy`` — two append-only tables (the
    Contract version and its first-class RequiredSignal children) pinned
    to an immutable definition version, exactly like Variant; no evidence
    binding/tracking-implementation/allocation/exposure/execution-
    authorization/result/winner column, and no freeze/status column of any
    kind) — and the MVP-40 governed Execution Authorization persistence
    contract (frozen Execution Authorization Discovery/Design Freeze;
    owned by ``strategy`` — one append-only, single-active-per-Experiment
    table pinning the exact Definition/Contract tips, plus its
    Variant-set snapshot children; no allocation/assignment/exposure/
    evidence-binding/tracking-validation/result/winner/validity/
    causality column, and no execution-started column of any kind). No
    Content
    Revision
    Request/Distribution/Paid Media/Measurement Cycle/Performance
    Snapshot/Agent Run/Handoff/Return/Gate
    Decision/IntegrationDefinition/WorkspaceIntegration/SubscriptionPlan/
    Subscription/CreativeBriefVersion/CampaignVersion/TrackingStatus/
    TrackingReadiness/Channel table has been prematurely introduced —
    BACKEND-15's persistence is deliberately limited to TrackingPlan/
    TrackingRequirement; TrackingPlan.status is the sole readiness
    source of truth, never a second persisted rollup (Governance
    Freeze). MVP-11B's own provenance tables are purely additive and
    never add a column to metric_entries/performance_observations/
    performance_signals/analysis_results themselves (MVP-11A-R2
    Governance repair)."""
    assert set(metadata.tables.keys()) == {
        "_infra_persistence_probe",
        "users",
        "user_preferences",
        "organizations",
        "workspaces",
        "memberships",
        "auth_sessions",
        "ai_preferences",
        "notification_preferences",
        "campaigns",
        "campaign_briefs",
        "campaign_runs",
        "run_stage_executions",
        "human_decision_requests",
        "human_decision_responses",
        "audit_events",
        "research_reports",
        "research_sources",
        "audience_profiles",
        "voc_evidence",
        "strategies",
        "positionings",
        "hypotheses",
        "experiments",
        "content_plans",
        "plan_items",
        "content_briefs",
        "content_pieces",
        "content_versions",
        "content_approvals",
        "content_distributions",
        "metric_entries",
        "metric_values",
        "performance_observations",
        "performance_signals",
        "analysis_results",
        "observation_metric_entries",
        "signal_observations",
        "analysis_result_signals",
        "creative_briefs",
        "assets",
        "asset_versions",
        "learning_candidates",
        "strategic_recommendation_candidates",
        "strategic_implications",
        "learning_derivations",
        "learning_qualifications",
        "learning_qualification_signals",
        "tracking_plans",
        "tracking_requirements",
        "measurement_analysis_runs",
        "measurement_analysis_run_metric_entries",
        "measurement_observation_derivations",
        "measurement_signal_derivations",
        "measurement_analysis_run_observation_usages",
        "measurement_analysis_run_signal_usages",
        "measurement_analysis_run_results",
        "distribution_metric_evidence",
        "content_distribution_tracking_requirements",
        "commercial_objectives",
        "offers",
        "strategic_decisions",
        "strategic_approvals",
        "strategy_revisions",
        "commercial_outcomes",
        "experiment_definition_versions",
        "experiment_variants",
        "measurement_contract_versions",
        "measurement_contract_signals",
        "execution_authorizations",
        "execution_authorization_variants",
        "execution_start_attestations",
    }


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://user@localhost/impulso_api_test",
        "postgresql+psycopg://user@127.0.0.1/impulso_api_test",
        "postgresql+psycopg://user@localhost/IMPULSO_API_TEST",  # case-insensitive
    ],
)
def test_safety_guard_accepts_local_test_scoped_urls(url: str) -> None:
    assert_safe_test_database_url(url)  # must not raise


@pytest.mark.parametrize(
    "url,reason",
    [
        ("postgresql+psycopg://user@localhost/impulso_api", "name without _test"),
        ("postgresql+psycopg://user@localhost/postgres", "name without _test"),
        ("postgresql+psycopg://user@db.example.com/impulso_api_test", "non-local host"),
        ("postgresql+psycopg://user@db.example.com/impulso_api", "neither"),
    ],
)
def test_safety_guard_rejects_unsafe_urls(url: str, reason: str) -> None:
    with pytest.raises(UnsafeTestDatabaseError):
        assert_safe_test_database_url(url)


def test_safety_guard_error_never_includes_credentials() -> None:
    url = "postgresql+psycopg://secret_user:secret_password@db.example.com/impulso_api"
    with pytest.raises(UnsafeTestDatabaseError) as excinfo:
        assert_safe_test_database_url(url)
    message = str(excinfo.value)
    assert "secret_user" not in message
    assert "secret_password" not in message
