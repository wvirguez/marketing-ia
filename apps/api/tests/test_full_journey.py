"""MVP-14B: full journey backend integration test (MVP-14A design).

Exercises the current production-reachable MVP campaign journey — auth ->
campaign creation -> START (deterministic bootstrap) -> Research/Audience/
Strategy/Plan/Content -> Assets/Tracking (truthful empty) -> manual metrics
-> measurement analysis -> learning derivation -> a lightweight Settings GET
— entirely through real public HTTP routes (never a direct service-layer
write), proving the CONNECTIONS across bounded contexts. It does not
re-test any single module's own already-covered behavior (each module has
its own dedicated, exhaustive test suite); assertions here are the minimum
needed to prove one continuous journey holds together end to end.

Assets/Tracking are asserted as truthful-empty only: no production caller
creates a CreativeBrief/Asset or TrackingPlan/TrackingRequirement anywhere
in this codebase (both are service-layer-only by explicit Governance
Freeze) — this test does not open that gap, per MVP-14A §17/§18.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.assets.models import Asset, CreativeBrief
from app.audit.models import ActorType, AuditEvent
from app.campaigns.models import CampaignRun
from app.campaigns.repository import CampaignRepository
from app.content.models import ContentApproval, ContentBrief, ContentPiece, ContentVersion
from app.learning.models import LearningCandidate, LearningCandidateStatus, LearningDerivation, StrategicRecommendationCandidate
from app.measurement.models import (
    AnalysisResult,
    MeasurementAnalysisRun,
    MeasurementAnalysisRunMetricEntry,
    MeasurementAnalysisRunResult,
    MetricEntry,
    PerformanceObservation,
    PerformanceSignal,
)
from app.planning.models import ContentPlan
from app.research.models import AudienceProfile, ResearchReport
from app.strategy.models import Strategy
from app.tracking.models import TrackingPlan
from tests.measurementtest import next_client_request_id
from tests.orchestrationtest import initialize_run, run_path, start_run

pytestmark = pytest.mark.postgres


def _campaign_path(fixtures: dict, suffix: str = "") -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}{suffix}"


def test_full_campaign_journey_from_creation_through_learning_and_settings(
    campaign_run_client: dict, db_session
) -> None:
    fixtures = campaign_run_client
    client = fixtures["client"]
    csrf = fixtures["csrf_token"]
    campaign_id = fixtures["campaign_id"]
    csrf_headers = {"X-CSRF-Token": csrf}

    # --- Campaign creation (already performed by the fixture via the real
    # POST /api/v1/campaigns) -------------------------------------------------
    assert campaign_id
    assert fixtures["run_id"]

    # --- Initialize + START (synchronous deterministic bootstrap) -----------
    initialize_run(fixtures)
    start_body = start_run(fixtures)
    assert start_body["status"] == "RUNNING", "CampaignRun.status must be RUNNING after START (not active execution)"

    stages_response = client.get(run_path(fixtures, "/stages"))
    assert stages_response.status_code == 200, stages_response.text
    by_stage = {s["stage"]: s["status"] for s in stages_response.json()["items"]}
    for completed_stage in ("RESEARCH", "AUDIENCE", "STRATEGY", "PLAN", "CONTENT"):
        assert by_stage[completed_stage] == "COMPLETED", f"[STAGES] {completed_stage} did not complete: {by_stage}"
    for pending_stage in ("CREATIVE", "DISTRIBUTION", "PAID_MEDIA", "TRACKING", "MEASUREMENT", "LEARNING"):
        assert by_stage[pending_stage] == "PENDING", f"[STAGES] {pending_stage} unexpectedly not PENDING: {by_stage}"

    # --- Research -------------------------------------------------------------
    research_response = client.get(_campaign_path(fixtures, "/research"))
    assert research_response.status_code == 200, research_response.text
    research_body = research_response.json()
    assert research_body["report"] is not None, "[RESEARCH] no ResearchReport was persisted"
    assert research_body["report"]["campaign_id"] == campaign_id, "[RESEARCH] wrong campaign association"
    assert research_body["sources"] == [], "[RESEARCH] bootstrap must fabricate no external sources"

    # --- Audience ---------------------------------------------------------------
    audience_response = client.get(_campaign_path(fixtures, "/audience"))
    assert audience_response.status_code == 200, audience_response.text
    audience_body = audience_response.json()
    assert audience_body["profile"] is not None, "[AUDIENCE] no AudienceProfile was persisted"
    assert audience_body["profile"]["campaign_id"] == campaign_id, "[AUDIENCE] wrong campaign association"
    assert audience_body["voc_evidence"] == [], "[AUDIENCE] bootstrap must fabricate no VOC evidence"

    # --- Strategy -----------------------------------------------------------------
    strategy_response = client.get(_campaign_path(fixtures, "/strategy"))
    assert strategy_response.status_code == 200, strategy_response.text
    strategy_body = strategy_response.json()
    assert strategy_body["strategy"] is not None, "[STRATEGY] no Strategy was persisted"
    assert len(strategy_body["hypotheses"]) >= 1, "[STRATEGY] no Hypothesis was persisted"
    assert any(h["status"] == "OPEN" for h in strategy_body["hypotheses"]), "[STRATEGY] no hypothesis remained OPEN"
    assert strategy_body["experiments"] == [], "[STRATEGY] no Experiment may be auto-created"

    # --- Plan -------------------------------------------------------------------
    plan_response = client.get(_campaign_path(fixtures, "/plan"))
    assert plan_response.status_code == 200, plan_response.text
    plan_body = plan_response.json()
    assert plan_body["plan"] is not None, "[PLAN] no ContentPlan was persisted"
    assert len(plan_body["items"]) == 2, "[PLAN] deterministic bootstrap must produce exactly 2 PlanItems"
    positioning = strategy_body["positioning"]
    if positioning is not None:
        assert positioning["statement"] in plan_body["plan"]["summary"], (
            "[PLAN] Planning must genuinely consume the persisted Strategy positioning"
        )

    # --- Content ------------------------------------------------------------------
    content_response = client.get(_campaign_path(fixtures, "/content"))
    assert content_response.status_code == 200, content_response.text
    content_items = content_response.json()["items"]
    assert len(content_items) == 2, "[CONTENT] deterministic bootstrap must produce exactly 2 ContentPieces"
    assert all(item["status"] == "DRAFT" for item in content_items), "[CONTENT] no ContentPiece may leave DRAFT here"
    content_id = content_items[0]["id"]

    # --- Assets (truthful empty) --------------------------------------------------
    assets_response = client.get(_campaign_path(fixtures, f"/content/{content_id}/assets"))
    assert assets_response.status_code == 200, assets_response.text
    assets_body = assets_response.json()
    assert assets_body["creative_brief"] is None, "[ASSETS] no CreativeBrief exists in current production"
    assert assets_body["assets"] == [], "[ASSETS] no Asset exists in current production"

    # --- Tracking (truthful empty) -------------------------------------------------
    tracking_response = client.get(_campaign_path(fixtures, "/tracking"))
    assert tracking_response.status_code == 200, tracking_response.text
    assert tracking_response.json()["plan"] is None, "[TRACKING] no TrackingPlan exists in current production"

    # --- Manual metrics: baseline + current period (a real Signal needs a
    # genuinely earlier, non-overlapping baseline period for the same
    # metric_name/channel — see app/measurement/analysis_pipeline.py::
    # _select_baseline) --------------------------------------------------------
    baseline_response = client.post(
        _campaign_path(fixtures, "/metrics"),
        json={
            "period_start": "2025-12-01",
            "period_end": "2025-12-31",
            "channel": "Instagram",
            "source": "MANUAL",
            "client_request_id": next_client_request_id(),
            "values": {"clicks": 100},
        },
        headers=csrf_headers,
    )
    assert baseline_response.status_code == 201, f"[METRICS baseline] {baseline_response.text}"
    baseline_metric_id = baseline_response.json()["id"]

    current_response = client.post(
        _campaign_path(fixtures, "/metrics"),
        json={
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "channel": "Instagram",
            "source": "MANUAL",
            "client_request_id": next_client_request_id(),
            "values": {"clicks": 150},
        },
        headers=csrf_headers,
    )
    assert current_response.status_code == 201, f"[METRICS current] {current_response.text}"
    current_metric_id = current_response.json()["id"]
    assert current_metric_id != baseline_metric_id, "[METRICS] the two entries must be distinct rows"

    metrics_list_response = client.get(_campaign_path(fixtures, "/metrics"))
    assert metrics_list_response.status_code == 200, metrics_list_response.text
    persisted_metric_ids = {item["id"] for item in metrics_list_response.json()["items"]}
    assert {baseline_metric_id, current_metric_id} <= persisted_metric_ids, "[METRICS] both entries must be readable via GET"

    # --- Measurement analysis run ---------------------------------------------------
    analysis_response = client.post(
        _campaign_path(fixtures, "/analysis/run"),
        json={"client_request_id": next_client_request_id()},
        headers=csrf_headers,
    )
    assert analysis_response.status_code == 200, f"[ANALYSIS] {analysis_response.text}"
    analysis_body = analysis_response.json()
    assert analysis_body["status"] == "COMPLETED", f"[ANALYSIS] run did not complete: {analysis_body}"
    assert analysis_body["failure_reason"] is None, "[ANALYSIS] a completed run must carry no failure_reason"
    assert analysis_body["campaign_id"] == campaign_id, "[ANALYSIS] wrong campaign association in response"

    # --- Measurement pipeline DB provenance (not exposed through HTTP) --------------
    campaign = CampaignRepository(db_session).get_by_public_id(campaign_id)

    run_row = db_session.execute(
        select(MeasurementAnalysisRun).where(MeasurementAnalysisRun.campaign_id == campaign.id)
    ).scalar_one()
    assert run_row.status.value == "COMPLETED", "[ANALYSIS DB] run row must be COMPLETED"

    run_entry_ids = {
        row.metric_entry_id
        for row in db_session.execute(
            select(MeasurementAnalysisRunMetricEntry).where(MeasurementAnalysisRunMetricEntry.analysis_run_id == run_row.id)
        ).scalars()
    }
    metric_entry_rows = db_session.execute(select(MetricEntry).where(MetricEntry.campaign_id == campaign.id)).scalars().all()
    assert len(metric_entry_rows) == 2, "[ANALYSIS DB] exactly the two journey MetricEntry rows must exist"
    assert {row.id for row in metric_entry_rows} == run_entry_ids, "[ANALYSIS DB] the run's own snapshot must reference both real MetricEntries"

    observations = db_session.execute(
        select(PerformanceObservation).where(PerformanceObservation.campaign_id == campaign.id)
    ).scalars().all()
    assert len(observations) >= 1, "[ANALYSIS DB] at least one PerformanceObservation must have been derived"

    signals = db_session.execute(
        select(PerformanceSignal).where(PerformanceSignal.campaign_id == campaign.id)
    ).scalars().all()
    assert len(signals) >= 1, "[ANALYSIS DB] at least one PerformanceSignal must have been derived (requires the baseline period)"

    analysis_result_rows = db_session.execute(
        select(AnalysisResult).where(AnalysisResult.campaign_id == campaign.id)
    ).scalars().all()
    assert len(analysis_result_rows) == 1, "[ANALYSIS DB] exactly one AnalysisResult must exist"
    analysis_result = analysis_result_rows[0]

    run_result_row = db_session.execute(
        select(MeasurementAnalysisRunResult).where(MeasurementAnalysisRunResult.analysis_result_id == analysis_result.id)
    ).scalar_one()
    assert run_result_row.analysis_run_id == run_row.id, "[ANALYSIS DB] the AnalysisResult must belong to this run"

    # --- Learning derivation ----------------------------------------------------
    derive_response = client.post(_campaign_path(fixtures, "/learning/derive"), headers=csrf_headers)
    assert derive_response.status_code == 200, f"[LEARNING derive] {derive_response.text}"
    derive_body = derive_response.json()
    assert len(derive_body["learning_candidates"]) == 1, "[LEARNING derive] exactly one candidate must be derived"
    derived_candidate = derive_body["learning_candidates"][0]
    assert derived_candidate["status"] == "CANDIDATE_IDENTIFIED", "[LEARNING derive] status must be CANDIDATE_IDENTIFIED"
    assert derive_body["strategic_recommendation_candidates"] == [], "[LEARNING derive] no StrategicRecommendationCandidate may be auto-created"
    candidate_id = derived_candidate["id"]

    # --- Learning GET ----------------------------------------------------------
    learning_get_response = client.get(_campaign_path(fixtures, "/learning"))
    assert learning_get_response.status_code == 200, learning_get_response.text
    learning_get_body = learning_get_response.json()
    matching = next((c for c in learning_get_body["learning_candidates"] if c["id"] == candidate_id), None)
    assert matching is not None, "[LEARNING get] the derived candidate must be visible through GET"
    assert matching["status"] == "CANDIDATE_IDENTIFIED", "[LEARNING get] status must remain CANDIDATE_IDENTIFIED"
    assert learning_get_body["strategic_recommendation_candidates"] == [], "[LEARNING get] no StrategicRecommendationCandidate may exist"

    # --- Learning provenance (DB) ------------------------------------------------
    candidate_row = db_session.execute(select(LearningCandidate).where(LearningCandidate.public_id == candidate_id)).scalar_one()
    derivation_row = db_session.execute(
        select(LearningDerivation).where(LearningDerivation.learning_candidate_id == candidate_row.id)
    ).scalar_one()
    assert derivation_row.analysis_result_id == analysis_result.id, "[LEARNING DB] LearningDerivation must reference this journey's AnalysisResult"
    assert candidate_row.analysis_result_id == analysis_result.id, "[LEARNING DB] LearningCandidate must reference this journey's AnalysisResult"
    assert candidate_row.workspace_id == analysis_result.workspace_id == campaign.workspace_id, "[LEARNING DB] workspace must match across the whole chain"

    # --- Settings: lightweight authenticated GET (proves the session/
    # workspace resolved throughout the entire journey remains valid) -----------
    session_response = client.get("/api/v1/auth/session")
    assert session_response.status_code == 200, session_response.text
    workspace_public_id = session_response.json()["workspace"]["id"]

    settings_response = client.get(f"/api/v1/workspaces/{workspace_public_id}/settings")
    assert settings_response.status_code == 200, f"[SETTINGS] {settings_response.text}"
    settings_body = settings_response.json()
    assert "workspace" in settings_body and "ai_preferences" in settings_body and "notifications" in settings_body

    # --- Governance negative assertions --------------------------------------------
    plan_row = db_session.execute(select(ContentPlan).where(ContentPlan.campaign_id == campaign.id)).scalar_one()
    briefs = db_session.execute(select(ContentBrief).where(ContentBrief.content_plan_id == plan_row.id)).scalars().all()
    assert len(briefs) == 2
    pieces = db_session.execute(select(ContentPiece).where(ContentPiece.content_brief_id.in_([b.id for b in briefs]))).scalars().all()
    assert len(pieces) == 2

    approval_count = db_session.execute(
        select(func.count())
        .select_from(ContentApproval)
        .join(ContentVersion, ContentApproval.content_version_id == ContentVersion.id)
        .where(ContentVersion.content_piece_id.in_([p.id for p in pieces]))
    ).scalar_one()
    assert approval_count == 0, "[GOVERNANCE] PRODUCED != APPROVED — zero ContentApproval expected"

    creative_brief_count = db_session.execute(
        select(func.count()).select_from(CreativeBrief).where(CreativeBrief.content_piece_id.in_([p.id for p in pieces]))
    ).scalar_one()
    assert creative_brief_count == 0, "[GOVERNANCE] zero CreativeBrief expected for the journey's content"

    asset_count = db_session.execute(
        select(func.count())
        .select_from(Asset)
        .join(CreativeBrief, Asset.creative_brief_id == CreativeBrief.id)
        .where(CreativeBrief.content_piece_id.in_([p.id for p in pieces]))
    ).scalar_one()
    assert asset_count == 0, "[GOVERNANCE] zero Asset expected for the journey's content"

    tracking_plan_count = db_session.execute(
        select(func.count()).select_from(TrackingPlan).where(TrackingPlan.campaign_id == campaign.id)
    ).scalar_one()
    assert tracking_plan_count == 0, "[GOVERNANCE] zero TrackingPlan expected for the journey's campaign"

    strategic_recommendation_count = db_session.execute(
        select(func.count())
        .select_from(StrategicRecommendationCandidate)
        .where(StrategicRecommendationCandidate.learning_candidate_id == candidate_row.id)
    ).scalar_one()
    assert strategic_recommendation_count == 0, "[GOVERNANCE] zero StrategicRecommendationCandidate expected"

    assert candidate_row.status is LearningCandidateStatus.CANDIDATE_IDENTIFIED, "[GOVERNANCE] CANDIDATE_IDENTIFIED != VALIDATED"

    # --- Tenancy assertions -------------------------------------------------------
    campaign_run_row = db_session.execute(select(CampaignRun).where(CampaignRun.campaign_id == campaign.id)).scalar_one()
    assert campaign.workspace_id == campaign_run_row.workspace_id, "[TENANCY] Campaign/CampaignRun workspace mismatch"

    research_row = db_session.execute(select(ResearchReport).where(ResearchReport.campaign_id == campaign.id)).scalar_one()
    assert research_row.campaign_id == campaign.id

    audience_row = db_session.execute(select(AudienceProfile).where(AudienceProfile.campaign_id == campaign.id)).scalar_one()
    assert audience_row.campaign_id == campaign.id

    strategy_row = db_session.execute(select(Strategy).where(Strategy.campaign_id == campaign.id)).scalar_one()
    assert strategy_row.campaign_id == campaign.id

    assert plan_row.campaign_id == campaign.id

    # --- Audit assertion (one high-value bootstrap event) --------------------------
    report_event = db_session.execute(
        select(AuditEvent).where(AuditEvent.campaign_id == campaign.id, AuditEvent.event_type == "research.report.recorded")
    ).scalar_one()
    assert report_event.actor_type is ActorType.SYSTEM, "[AUDIT] deterministic bootstrap events must be attributed to SYSTEM, never AGENT"
