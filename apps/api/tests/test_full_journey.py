"""MVP-14B: full journey backend integration test (MVP-14A design),
extended through the Content lifecycle + human-in-the-loop approval
workflow by MVP-17B.

Exercises the current production-reachable MVP campaign journey — auth ->
campaign creation -> START (deterministic bootstrap) -> Research/Audience/
Strategy/Plan/Content -> Content lifecycle + human-in-the-loop approval
(MVP-17B approval, then MVP-18B Distribution) -> Assets/Tracking (truthful empty) -> manual
metrics -> measurement analysis -> learning derivation -> a lightweight
Settings GET — entirely through real public HTTP routes (never a direct
service-layer write), proving the CONNECTIONS across bounded contexts. It
does not re-test any single module's own already-covered behavior (each
module has its own dedicated, exhaustive test suite); assertions here are
the minimum needed to prove one continuous journey holds together end to
end.

The Content approval decision below proves the human-in-the-loop
APPLICATION workflow only — the journey's own authenticated human user
(OWNER of their own freshly-created workspace) records the decision; this
does not claim AGENT-00 itself technically executed anything (see
``app/content/service.py``'s own module docstring, and MVP-17A-R1). The
journey records a human-reported external distribution event; no external
publishing is performed by this application.

Assets/Tracking are asserted as truthful-empty only: no production caller
creates a CreativeBrief/Asset or TrackingPlan/TrackingRequirement anywhere
in this codebase (both are service-layer-only by explicit Governance
Freeze) — this test does not open that gap, per MVP-14A §17/§18.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.assets.models import Asset, CreativeBrief
from app.audit.models import ActorType, AuditEvent
from app.campaigns.models import CampaignRun
from app.campaigns.repository import CampaignRepository
from app.content.models import ContentApproval, ContentBrief, ContentDistribution, ContentPiece, ContentPieceStatus, ContentVersion
from app.learning.models import LearningCandidate, LearningCandidateStatus, LearningDerivation, StrategicRecommendationCandidate
from app.measurement.models import (
    AnalysisResult,
    DistributionMetricEvidence,
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

    # --- Content lifecycle + human-in-the-loop approval (MVP-17B) -----------------
    # Advances only the first ContentPiece through the full lifecycle +
    # approval workflow, followed by human-recorded Distribution. The
    # second ContentPiece is deliberately left untouched at DRAFT.
    content_path = _campaign_path(fixtures, f"/content/{content_id}")

    in_production_response = client.post(f"{content_path}/mark-in-production", headers=csrf_headers)
    assert in_production_response.status_code == 200, f"[CONTENT LIFECYCLE] {in_production_response.text}"
    assert in_production_response.json()["piece"]["status"] == "IN_PRODUCTION"

    produced_response = client.post(f"{content_path}/mark-produced", headers=csrf_headers)
    assert produced_response.status_code == 200, f"[CONTENT LIFECYCLE] {produced_response.text}"
    assert produced_response.json()["piece"]["status"] == "PRODUCED"

    ready_response = client.post(f"{content_path}/mark-ready-for-review", headers=csrf_headers)
    assert ready_response.status_code == 200, f"[CONTENT LIFECYCLE] {ready_response.text}"
    assert ready_response.json()["piece"]["status"] == "READY_FOR_REVIEW"
    v1_id = ready_response.json()["latest_version"]["id"]

    request_approval_response = client.post(f"{content_path}/request-approval", headers=csrf_headers)
    assert request_approval_response.status_code == 201, f"[CONTENT APPROVAL] {request_approval_response.text}"
    approval_id = request_approval_response.json()["latest_approval"]["id"]
    assert request_approval_response.json()["latest_approval"]["status"] == "REQUESTED"

    under_review_response = client.post(f"{content_path}/approvals/{approval_id}/mark-under-review", headers=csrf_headers)
    assert under_review_response.status_code == 200, f"[CONTENT APPROVAL] {under_review_response.text}"
    assert under_review_response.json()["latest_approval"]["status"] == "UNDER_REVIEW"

    # --- Revision loop (MVP-20): the reviewer requests changes, the
    # producer revises, and only the revised version can be resubmitted.
    # Recorded by the journey's own authenticated human user (OWNER of
    # their own workspace) — an application-level decision, not a claim
    # that AGENT-00 itself executed the governance gate.
    changes_requested_response = client.post(
        f"{content_path}/approvals/{approval_id}/decision", json={"decision": "CHANGES_REQUESTED"}, headers=csrf_headers
    )
    assert changes_requested_response.status_code == 200, f"[REVISION] {changes_requested_response.text}"
    assert changes_requested_response.json()["latest_approval"]["status"] == "CHANGES_REQUESTED"
    assert changes_requested_response.json()["piece"]["status"] == "REVISION_REQUESTED"

    # The generic mark-in-production bypass must be rejected — the only
    # legitimate way out of REVISION_REQUESTED is creating a new Version.
    bypass_response = client.post(f"{content_path}/mark-in-production", headers=csrf_headers)
    assert bypass_response.status_code == 409, f"[REVISION] mark-in-production bypass was not rejected: {bypass_response.text}"
    stale_resubmit_response = client.post(f"{content_path}/request-approval", headers=csrf_headers)
    assert stale_resubmit_response.status_code == 409, f"[REVISION] stale resubmission was not rejected: {stale_resubmit_response.text}"

    create_version_response = client.post(
        f"{content_path}/versions",
        json={"payload": {"kind": "reel", "hook": "Revised hook after feedback.", "scenes": [], "caption": "Revised.", "hashtags": []}},
        headers=csrf_headers,
    )
    assert create_version_response.status_code == 201, f"[REVISION] {create_version_response.text}"
    v2_id = create_version_response.json()["latest_version"]["id"]
    assert v2_id != v1_id, "[REVISION] a genuinely new immutable ContentVersion must be created"
    assert create_version_response.json()["piece"]["status"] == "IN_PRODUCTION"

    revised_produced_response = client.post(f"{content_path}/mark-produced", headers=csrf_headers)
    assert revised_produced_response.status_code == 200, f"[REVISION] {revised_produced_response.text}"

    revised_ready_response = client.post(f"{content_path}/mark-ready-for-review", headers=csrf_headers)
    assert revised_ready_response.status_code == 200, f"[REVISION] {revised_ready_response.text}"
    assert revised_ready_response.json()["latest_version"]["id"] == v2_id

    resubmit_response = client.post(f"{content_path}/request-approval", headers=csrf_headers)
    assert resubmit_response.status_code == 201, f"[REVISION] {resubmit_response.text}"
    approval_id_2 = resubmit_response.json()["latest_approval"]["id"]
    assert approval_id_2 != approval_id, "[REVISION] the resubmission must be a genuinely new Approval, not the closed A1"

    under_review_response_2 = client.post(f"{content_path}/approvals/{approval_id_2}/mark-under-review", headers=csrf_headers)
    assert under_review_response_2.status_code == 200, f"[REVISION] {under_review_response_2.text}"

    decision_response = client.post(
        f"{content_path}/approvals/{approval_id_2}/decision", json={"decision": "APPROVED"}, headers=csrf_headers
    )
    assert decision_response.status_code == 200, f"[CONTENT APPROVAL] {decision_response.text}"
    decision_body = decision_response.json()
    assert decision_body["latest_approval"]["status"] == "APPROVED"
    assert decision_body["latest_approval"]["decided_at"] is not None
    assert decision_body["piece"]["status"] == "APPROVED"
    assert decision_body["latest_version"]["id"] == v2_id, "[REVISION] the APPROVED cycle must be for V2, not the original V1"
    mark_distribution_response = client.post(
        f"{content_path}/distribution/mark-ready-for-distribution", headers=csrf_headers
    )
    assert mark_distribution_response.status_code == 201, mark_distribution_response.text
    assert mark_distribution_response.json()["piece"]["status"] == "READY_FOR_DISTRIBUTION"
    assert mark_distribution_response.json()["distribution"]["status"] == "READY"
    record_distribution_response = client.post(
        f"{content_path}/distribution/record-distributed",
        json={"external_reference": "opaque-reference-123"}, headers=csrf_headers,
    )
    assert record_distribution_response.status_code == 200, record_distribution_response.text
    assert record_distribution_response.json()["piece"]["status"] == "DISTRIBUTED"
    assert record_distribution_response.json()["distribution"]["status"] == "DISTRIBUTED"
    assert record_distribution_response.json()["distribution"]["distributed_at"] is not None

    # --- Revision-loop traceability + provenance (MVP-20 §61/§62/§81) --------
    # Distribution must freeze the *revised, approved* V2 — never the
    # original, CHANGES_REQUESTED V1 — with no MVP-18 redesign required.
    v1_row = db_session.execute(select(ContentVersion).where(ContentVersion.public_id == v1_id)).scalar_one()
    v2_row = db_session.execute(select(ContentVersion).where(ContentVersion.public_id == v2_id)).scalar_one()
    assert v1_row.id != v2_row.id
    distribution_row = db_session.execute(
        select(ContentDistribution).where(ContentDistribution.content_piece_id == v2_row.content_piece_id)
    ).scalar_one()
    assert distribution_row.content_version_id == v2_row.id, "[REVISION] Distribution must freeze V2, not V1"

    a1_row = db_session.execute(select(ContentApproval).where(ContentApproval.public_id == approval_id)).scalar_one()
    a2_row = db_session.execute(select(ContentApproval).where(ContentApproval.public_id == approval_id_2)).scalar_one()
    assert a1_row.content_version_id == v1_row.id, "[REVISION] A1 must remain permanently linked to V1"
    assert a2_row.content_version_id == v2_row.id, "[REVISION] A2 must be linked to V2"
    assert a1_row.status.value == "CHANGES_REQUESTED", "[REVISION] A1 must remain unchanged, never overwritten"
    stale_approvals_against_v1 = db_session.execute(
        select(func.count()).select_from(ContentApproval).where(ContentApproval.content_version_id == v1_row.id)
    ).scalar_one()
    assert stale_approvals_against_v1 == 1, "[REVISION] no second Approval may ever exist against the stale V1"

    # --- Distribution-linked Measurement Evidence (MVP-19B): human-reported
    # metrics for THIS Distribution — never a claim of attribution/causation.
    today = datetime.now(timezone.utc).date()
    evidence_response = client.post(
        f"{content_path}/distribution/evidence",
        json={
            "period_start": (today - timedelta(days=3)).isoformat(),
            "period_end": today.isoformat(),
            "values": {"reach": 750, "saves": 30},
            "client_request_id": next_client_request_id(),
            "source_reference": "creator dashboard screenshot",
        },
        headers=csrf_headers,
    )
    assert evidence_response.status_code == 201, f"[EVIDENCE] {evidence_response.text}"
    evidence_body = evidence_response.json()
    assert evidence_body["evidence_scope"] == "DISTRIBUTION_SPECIFIC"
    assert evidence_body["source"] == "MANUAL"
    assert evidence_body["distribution_id"] == record_distribution_response.json()["distribution"]["id"]
    assert evidence_body["is_current"] is True
    evidence_metric_entry_id = evidence_body["metric_entry_id"]

    evidence_list_response = client.get(f"{content_path}/distribution/evidence")
    assert evidence_list_response.status_code == 200, evidence_list_response.text
    assert evidence_list_response.json()["total"] == 1
    assert evidence_list_response.json()["items"][0]["id"] == evidence_body["id"]

    # --- Distribution Evidence Summary (MVP-21): read-only, descriptive,
    # server-computed — never recomputed by the client, no arithmetic
    # aggregation across reports.
    summary_response = client.get(f"{content_path}/distribution/evidence/summary")
    assert summary_response.status_code == 200, summary_response.text
    summary_body = summary_response.json()
    assert summary_body["content_piece_id"] == content_id
    assert summary_body["distribution_id"] == record_distribution_response.json()["distribution"]["id"]
    assert summary_body["content_version_id"] == v2_id, "[SUMMARY] must freeze the Distribution's own version (V2), never Piece.latest_version"
    assert summary_body["channel"] == record_distribution_response.json()["distribution"]["channel"]
    assert {m["metric_name"] for m in summary_body["metrics"]} == {"reach", "saves"}
    reach_metric = next(m for m in summary_body["metrics"] if m["metric_name"] == "reach")
    assert reach_metric["report_count"] == 1
    assert reach_metric["latest_value"] == "750.0000"
    assert reach_metric["earliest_value"] == "750.0000"
    # A correction-chain assertion is deliberately NOT added here — this
    # journey's later assertions count MetricEntry/AuditEvent rows exactly,
    # and a correction here would be additive scope creep into an
    # already-fragile shared test; dedicated correction-chain coverage
    # lives in tests/test_distribution_evidence_summary_api.py instead.

    # --- Campaign Distribution Evidence Rollup (MVP-22): a real,
    # end-to-end smoke check that the Campaign-wide route works correctly
    # against this journey's actual single-Distribution Evidence — never
    # recomputed by the client, no arithmetic aggregation. A genuine
    # second, independently-DISTRIBUTED Piece (proving cross-Distribution
    # membership/report_count/provenance) is deliberately NOT added here:
    # this journey's own governance assertion later requires the second
    # bootstrap ContentPiece to remain untouched at DRAFT
    # ("[GOVERNANCE] the second ContentPiece must remain untouched at
    # DRAFT"), and advancing it here would break that invariant merely to
    # duplicate coverage that already exists, independently and more
    # thoroughly, in tests/test_campaign_distribution_evidence_rollup_api.py.
    rollup_response = client.get(_campaign_path(fixtures, "/distribution/evidence/summary"))
    assert rollup_response.status_code == 200, rollup_response.text
    rollup_body = rollup_response.json()
    assert rollup_body["campaign_id"] == campaign_id
    rollup_reach = next(m for m in rollup_body["metrics"] if m["metric_name"] == "reach")
    assert rollup_reach["report_count"] == 1
    assert rollup_reach["latest"]["value"] == "750.0000"
    assert rollup_reach["latest"]["content_piece_id"] == content_id
    assert rollup_reach["latest"]["distribution_id"] == record_distribution_response.json()["distribution"]["id"]
    assert rollup_reach["latest"]["content_version_id"] == v2_id
    assert rollup_reach["latest"]["channel"] == record_distribution_response.json()["distribution"]["channel"]

    # Statuses remain exactly as Distribution left them — Evidence never
    # touches ContentPiece.status or ContentDistribution.status.
    unchanged_detail = client.get(content_path)
    assert unchanged_detail.json()["piece"]["status"] == "DISTRIBUTED"
    assert unchanged_detail.json()["distribution"]["status"] == "DISTRIBUTED"

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
    assert evidence_metric_entry_id not in persisted_metric_ids, "[EVIDENCE AGGREGATE ISOLATION] Evidence-linked entry must not appear in the aggregate /metrics listing"

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
    assert len(metric_entry_rows) == 3, "[ANALYSIS DB] baseline + current aggregate entries + one Evidence-linked entry"
    assert {row.id for row in metric_entry_rows if row.public_id != evidence_metric_entry_id} == run_entry_ids, (
        "[ANALYSIS DB] the run's own snapshot must reference exactly the two aggregate MetricEntries"
    )
    evidence_entry_row = next(row for row in metric_entry_rows if row.public_id == evidence_metric_entry_id)
    assert evidence_entry_row.id not in run_entry_ids, "[EVIDENCE ANALYSIS ISOLATION] Evidence-linked entry must never feed the analysis pipeline"

    # --- Distribution-linked Measurement Evidence DB provenance ---------------------
    evidence_row = db_session.execute(
        select(DistributionMetricEvidence).where(DistributionMetricEvidence.metric_entry_id == evidence_entry_row.id)
    ).scalar_one()
    assert evidence_row.distribution_id is not None
    assert evidence_row.workspace_id == campaign.workspace_id
    assert evidence_row.correction_reason is None
    assert evidence_row.supersedes_evidence_id is None

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
    # MVP-20: exactly two now — A1 (CHANGES_REQUESTED, against V1) and A2
    # (APPROVED, against the revised V2), the journey's own human-in-the-loop
    # revision cycle. Neither is a stray/unexpected Approval.
    assert approval_count == 2, "[GOVERNANCE] exactly two ContentApprovals expected — the journey's revision cycle (A1 + A2)"

    version_count = db_session.execute(
        select(func.count()).select_from(ContentVersion).where(ContentVersion.content_piece_id.in_([p.id for p in pieces]))
    ).scalar_one()
    # 2 initial Versions (one per bootstrapped ContentPiece) + 1 revision
    # Version (V2) created during the journey's own revision cycle.
    assert version_count == 3, "[GOVERNANCE] exactly three ContentVersions expected (2 initial + 1 revision)"

    approved_piece_count = db_session.execute(
        select(func.count())
        .select_from(ContentPiece)
        .where(ContentPiece.id.in_([p.id for p in pieces]), ContentPiece.status == ContentPieceStatus.DISTRIBUTED)
    ).scalar_one()
    assert approved_piece_count == 1, "[GOVERNANCE] exactly one ContentPiece must have reached DISTRIBUTED"
    distributed_piece = next(piece for piece in pieces if piece.public_id == content_id)
    other_piece = next(piece for piece in pieces if piece.public_id != content_id)
    distribution = db_session.execute(select(ContentDistribution).where(ContentDistribution.content_piece_id == distributed_piece.id)).scalar_one()
    # MVP-20: this Piece now carries two ContentVersions (V1, revised V2)
    # and two ContentApprovals (A1 CHANGES_REQUESTED, A2 APPROVED) — scope
    # to the specific Version Distribution actually froze, never "the"
    # Approval for the whole Piece.
    approval = db_session.execute(
        select(ContentApproval).where(ContentApproval.content_version_id == distribution.content_version_id)
    ).scalar_one()
    assert distribution.content_version_id == approval.content_version_id
    assert approval.status.value == "APPROVED"
    assert distribution.channel == distributed_piece.channel
    assert distribution.distributed_at is not None
    assert db_session.execute(select(func.count()).select_from(ContentDistribution).where(ContentDistribution.content_piece_id == other_piece.id)).scalar_one() == 0
    distribution_events = db_session.execute(select(AuditEvent).where(AuditEvent.distribution_id == distribution.id)).scalars().all()
    assert {event.event_type for event in distribution_events} == {
        "content.distribution.ready_recorded", "content.distribution.recorded", "content.piece.status_changed",
        "measurement.distribution_evidence.recorded",
    }
    assert all(event.actor_type is ActorType.USER and event.actor_user_id is not None for event in distribution_events)
    draft_piece_count = db_session.execute(
        select(func.count())
        .select_from(ContentPiece)
        .where(ContentPiece.id.in_([p.id for p in pieces]), ContentPiece.status == ContentPieceStatus.DRAFT)
    ).scalar_one()
    assert draft_piece_count == 1, "[GOVERNANCE] the second ContentPiece must remain untouched at DRAFT"

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
