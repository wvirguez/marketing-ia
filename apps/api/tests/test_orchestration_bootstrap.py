"""MVP-04/MVP-04R/MVP-05E deterministic orchestration bootstrap: RESEARCH ->
AUDIENCE -> STRATEGY -> PLAN -> CONTENT. All marked `postgres`.

Preserves every non-negotiable from the MVP-04 implementation gate plus the
MVP-05E extension: no Assets/Tracking/Measurement/Learning row, no
ContentApproval row and no ContentPiece status past DRAFT, no fabricated
evidence/VOC, no auto-confirmed Hypothesis, no ActorType.AGENT, no
FAILED->READY retry, no duplicate artifact on a second (rejected) START.
Content-specific bootstrap scenarios (per-PlanItem materialization,
idempotency, partial failure, GET readback) live in
``tests/test_content_orchestration_bootstrap.py``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.audit.models import ActorType, AuditEvent
from app.campaigns.models import CampaignRunStatus
from app.campaigns.repository import CampaignBriefRepository, CampaignRepository, CampaignRunRepository
from app.content.models import ContentApproval, ContentBrief, ContentPiece, ContentPieceStatus, ContentVersion
from app.measurement.models import MetricEntry
from app.orchestration.models import StageExecutionStatus
from app.orchestration.repository import RunStageExecutionRepository
from app.orchestration.service import OrchestrationService
from app.planning.models import ContentPlan, PlanItem
from app.research.models import AudienceProfile, ResearchReport, ResearchSource, VOCEvidence
from app.strategy.models import Experiment, Hypothesis, HypothesisStatus, Positioning, Strategy
from app.strategy.service import StrategyService
from app.tracking.models import TrackingPlan
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.orchestrationtest import initialize_run, run_path, start_run

pytestmark = pytest.mark.postgres


def _build_campaign_run(session, *, org_name: str, workspace_name: str, campaign_name: str):
    organization = OrganizationRepository(session).create(name=org_name)
    workspace = WorkspaceRepository(session).create(organization_id=organization.id, name=workspace_name)
    campaign = CampaignRepository(session).create(workspace_id=workspace.id, name=campaign_name)
    run = CampaignRunRepository(session).create(campaign=campaign, run_number=1)
    session.flush()
    return organization, workspace, campaign, run


# --- success path (§26) -----------------------------------------------------


def test_start_bootstraps_research_through_content_and_leaves_creative_pending(campaign_run_client: dict) -> None:
    """MVP-05E: extends the MVP-04/04R chain by one stage. CONTENT now
    completes alongside RESEARCH/AUDIENCE/STRATEGY/PLAN; CREATIVE and
    everything after it remains the out-of-scope PENDING boundary."""
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    stages = campaign_run_client["client"].get(run_path(campaign_run_client, "/stages")).json()["items"]
    by_stage = {s["stage"]: s["status"] for s in stages}
    assert by_stage["RESEARCH"] == "COMPLETED"
    assert by_stage["AUDIENCE"] == "COMPLETED"
    assert by_stage["STRATEGY"] == "COMPLETED"
    assert by_stage["PLAN"] == "COMPLETED"
    assert by_stage["CONTENT"] == "COMPLETED"
    for pending_stage in ("CREATIVE", "DISTRIBUTION", "PAID_MEDIA", "TRACKING", "MEASUREMENT", "LEARNING"):
        assert by_stage[pending_stage] == "PENDING"

    run_detail = campaign_run_client["client"].get(run_path(campaign_run_client)).json()
    assert run_detail["status"] == "RUNNING"


def test_bootstrap_persists_one_real_domain_output_per_stage(campaign_run_client: dict, db_session) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    campaign = CampaignRepository(db_session).get_by_public_id(campaign_run_client["campaign_id"])

    assert db_session.execute(
        select(func.count()).select_from(ResearchReport).where(ResearchReport.campaign_id == campaign.id)
    ).scalar_one() == 1
    assert db_session.execute(
        select(func.count()).select_from(AudienceProfile).where(AudienceProfile.campaign_id == campaign.id)
    ).scalar_one() == 1
    assert db_session.execute(
        select(func.count()).select_from(Strategy).where(Strategy.campaign_id == campaign.id)
    ).scalar_one() == 1
    assert db_session.execute(
        select(func.count()).select_from(ContentPlan).where(ContentPlan.campaign_id == campaign.id)
    ).scalar_one() == 1
    # MVP-05E: one ContentBrief + ContentPiece + ContentVersion per
    # PlanItem — build_plan_content always produces exactly 2 items.
    plan = db_session.execute(select(ContentPlan).where(ContentPlan.campaign_id == campaign.id)).scalar_one()
    plan_items = db_session.execute(select(PlanItem).where(PlanItem.content_plan_id == plan.id)).scalars().all()
    assert len(plan_items) == 2
    assert db_session.execute(
        select(func.count()).select_from(ContentBrief).where(ContentBrief.content_plan_id == plan.id)
    ).scalar_one() == 2
    briefs = db_session.execute(select(ContentBrief).where(ContentBrief.content_plan_id == plan.id)).scalars().all()
    assert db_session.execute(
        select(func.count()).select_from(ContentPiece).where(ContentPiece.content_brief_id.in_([b.id for b in briefs]))
    ).scalar_one() == 2
    pieces = db_session.execute(
        select(ContentPiece).where(ContentPiece.content_brief_id.in_([b.id for b in briefs]))
    ).scalars().all()
    assert db_session.execute(
        select(func.count()).select_from(ContentVersion).where(ContentVersion.content_piece_id.in_([p.id for p in pieces]))
    ).scalar_one() == 2


# --- STRATEGY -> PLANNING dependency (MVP-04R) ------------------------------


def test_plan_content_incorporates_the_persisted_strategy_positioning(campaign_run_client: dict, db_session) -> None:
    """Proves the MVP-04R repair: Planning must consume the actual persisted
    Strategy output, not merely CampaignBrief a second time. The
    Positioning statement is synthesized text unique to Strategy's own
    output (it never appears verbatim in CampaignBrief) — its presence in
    Planning's persisted ContentPlan/PlanItem rows is only possible if
    Planning genuinely read the persisted Strategy."""
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    campaign = CampaignRepository(db_session).get_by_public_id(campaign_run_client["campaign_id"])
    strategy = db_session.execute(select(Strategy).where(Strategy.campaign_id == campaign.id)).scalar_one()
    positioning = db_session.execute(
        select(Positioning).where(Positioning.strategy_id == strategy.id)
    ).scalar_one()

    plan = db_session.execute(select(ContentPlan).where(ContentPlan.campaign_id == campaign.id)).scalar_one()
    items = db_session.execute(select(PlanItem).where(PlanItem.content_plan_id == plan.id)).scalars().all()

    assert positioning.statement in plan.summary
    assert any(positioning.statement in item.objective for item in items)


def test_plan_fails_without_persisted_strategy_and_creates_no_content_plan(db_session) -> None:
    """No-fallback guard (MVP-04R): if Planning cannot obtain its required
    persisted Strategy dependency, it must NOT silently synthesize from
    CampaignBrief alone. STRATEGY itself is left to complete for real (its
    row count stays 1) — only Planning's own read of that persisted output
    is made to fail, using the same patch.object seam the existing
    strategy-failure test already uses."""
    _org, _ws, campaign, run = _build_campaign_run(
        db_session, org_name="No Strategy Org", workspace_name="No Strategy WS", campaign_name="No Strategy Campaign"
    )
    CampaignBriefRepository(db_session).create(
        campaign_id=campaign.id, version=1, prompt="Idea sin estrategia obtenible.",
        product_type="Curso online", price=None, audience="Principiantes", budget=None, channel="Instagram",
    )
    db_session.commit()

    service = OrchestrationService(db_session)
    service.initialize_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)
    service.start_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    with patch.object(StrategyService, "get_strategy_output", return_value=(None, None, [], [])):
        with pytest.raises(RuntimeError, match="no persisted Strategy"):
            service.run_deterministic_bootstrap(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    db_session.rollback()
    stages = {
        s.stage.value: s.status
        for s in RunStageExecutionRepository(db_session).list_for_run(campaign_run_id=run.id)
    }
    assert stages["RESEARCH"] == StageExecutionStatus.COMPLETED
    assert stages["AUDIENCE"] == StageExecutionStatus.COMPLETED
    assert stages["STRATEGY"] == StageExecutionStatus.COMPLETED
    assert stages["PLAN"] == StageExecutionStatus.FAILED
    assert stages["CONTENT"] == StageExecutionStatus.PENDING

    db_session.refresh(run)
    assert run.status is CampaignRunStatus.RUNNING

    # The real Strategy WAS actually persisted (never mocked away) — this
    # is a genuine "could not obtain my dependency" failure, not evidence
    # that Strategy itself never ran.
    assert db_session.execute(
        select(func.count()).select_from(Strategy).where(Strategy.campaign_id == campaign.id)
    ).scalar_one() == 1
    assert db_session.execute(
        select(func.count()).select_from(ContentPlan).where(ContentPlan.campaign_id == campaign.id)
    ).scalar_one() == 0


# --- synthetic evidence protection (§27) ------------------------------------


def test_bootstrap_fabricates_no_sources_no_voc_and_keeps_hypotheses_open(campaign_run_client: dict, db_session) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    campaign = CampaignRepository(db_session).get_by_public_id(campaign_run_client["campaign_id"])
    report = db_session.execute(
        select(ResearchReport).where(ResearchReport.campaign_id == campaign.id)
    ).scalar_one()
    profile = db_session.execute(
        select(AudienceProfile).where(AudienceProfile.campaign_id == campaign.id)
    ).scalar_one()
    strategy = db_session.execute(select(Strategy).where(Strategy.campaign_id == campaign.id)).scalar_one()

    assert db_session.execute(
        select(func.count()).select_from(ResearchSource).where(ResearchSource.research_report_id == report.id)
    ).scalar_one() == 0
    assert db_session.execute(
        select(func.count()).select_from(VOCEvidence).where(VOCEvidence.audience_profile_id == profile.id)
    ).scalar_one() == 0

    hypotheses = db_session.execute(select(Hypothesis).where(Hypothesis.strategy_id == strategy.id)).scalars().all()
    assert len(hypotheses) >= 1
    assert all(h.status is HypothesisStatus.OPEN for h in hypotheses)
    assert db_session.execute(
        select(func.count()).select_from(Experiment).where(Experiment.hypothesis_id.in_([h.id for h in hypotheses]))
    ).scalar_one() == 0

    # No fabricated URL/locator/claim anywhere in the persisted narrative,
    # and an explicit, honest draft/no-external-evidence disclosure.
    for text in (report.summary, profile.summary, strategy.summary):
        assert "http://" not in text and "https://" not in text
        assert "borrador" in text.lower()
        assert "no ha sido validado" in text.lower() or "sin validar" in text.lower() or "no validad" in text.lower()


# --- content approval protection (§28) --------------------------------------


def test_bootstrap_content_pieces_remain_draft_with_zero_approvals(campaign_run_client: dict, db_session) -> None:
    """MVP-05E governance invariant: deterministic Content synthesis alone
    must never advance ContentPiece.status past its DRAFT creation default,
    and must never create a ContentApproval row. Scoped to this test's own
    ContentPlan — a raw table-wide COUNT(*) would pick up rows committed by
    unrelated tests sharing the same Postgres database across the full
    suite run."""
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    campaign = CampaignRepository(db_session).get_by_public_id(campaign_run_client["campaign_id"])
    plan = db_session.execute(select(ContentPlan).where(ContentPlan.campaign_id == campaign.id)).scalar_one()
    briefs = db_session.execute(select(ContentBrief).where(ContentBrief.content_plan_id == plan.id)).scalars().all()
    assert len(briefs) == 2

    pieces = db_session.execute(
        select(ContentPiece).where(ContentPiece.content_brief_id.in_([b.id for b in briefs]))
    ).scalars().all()
    assert len(pieces) == 2
    assert all(p.status is ContentPieceStatus.DRAFT for p in pieces)

    assert db_session.execute(
        select(func.count())
        .select_from(ContentApproval)
        .join(ContentVersion, ContentApproval.content_version_id == ContentVersion.id)
        .where(ContentVersion.content_piece_id.in_([p.id for p in pieces]))
    ).scalar_one() == 0


def test_bootstrap_creates_zero_tracking_and_metric_rows_for_its_own_campaign(campaign_run_client: dict, db_session) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    campaign = CampaignRepository(db_session).get_by_public_id(campaign_run_client["campaign_id"])
    assert db_session.execute(
        select(func.count()).select_from(TrackingPlan).where(TrackingPlan.campaign_id == campaign.id)
    ).scalar_one() == 0
    assert db_session.execute(
        select(func.count()).select_from(MetricEntry).where(MetricEntry.campaign_id == campaign.id)
    ).scalar_one() == 0


def test_bootstrap_code_never_references_content_approval_tracking_measurement_or_learning_write_methods() -> None:
    """Structural boundary check, immune to shared-database scoping
    concerns entirely: the bootstrap module/service source itself must
    contain no reference to any Content-production-transition or
    Content-approval command (MVP-05E authorizes ``record_brief``/
    ``record_piece`` only — never a status transition past the DRAFT
    creation default, never an approval), nor any Tracking, Measurement,
    Learning, Creative, or Asset write command."""
    import inspect

    from app.orchestration import bootstrap as bootstrap_module
    from app.orchestration import service as orchestration_service_module

    forbidden = (
        "mark_in_production", "mark_produced",
        "mark_ready_for_review", "archive_piece", "request_approval", "mark_under_review",
        "record_authorized_approval_decision",
        "record_tracking_plan", "record_tracking_requirement",
        "transition_tracking_plan", "update_tracking_requirement_status",
        "record_metric_entry", "record_observation", "record_signal", "record_analysis_result",
        "record_learning_candidate", "transition_learning_candidate",
        "record_strategic_recommendation_candidate", "decide_strategic_recommendation_candidate",
        "record_creative_brief", "record_asset", "record_asset_version", "archive_asset",
    )
    source = inspect.getsource(bootstrap_module) + inspect.getsource(orchestration_service_module)
    for name in forbidden:
        assert name not in source, name


# --- duplicate protection (§29) ---------------------------------------------


def test_second_start_is_rejected_and_creates_no_duplicate_output(campaign_run_client: dict, db_session) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    second = campaign_run_client["client"].post(
        run_path(campaign_run_client, "/start"), headers={"X-CSRF-Token": campaign_run_client["csrf_token"]}
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "INVALID_LIFECYCLE_TRANSITION"

    campaign = CampaignRepository(db_session).get_by_public_id(campaign_run_client["campaign_id"])
    assert db_session.execute(
        select(func.count()).select_from(ResearchReport).where(ResearchReport.campaign_id == campaign.id)
    ).scalar_one() == 1
    assert db_session.execute(
        select(func.count()).select_from(AudienceProfile).where(AudienceProfile.campaign_id == campaign.id)
    ).scalar_one() == 1
    assert db_session.execute(
        select(func.count()).select_from(Strategy).where(Strategy.campaign_id == campaign.id)
    ).scalar_one() == 1
    assert db_session.execute(
        select(func.count()).select_from(ContentPlan).where(ContentPlan.campaign_id == campaign.id)
    ).scalar_one() == 1


# --- failure path (§30) ------------------------------------------------------


def test_audience_failure_leaves_research_completed_and_stops_the_bootstrap(db_session) -> None:
    _org, _ws, campaign, run = _build_campaign_run(
        db_session, org_name="Failure Org", workspace_name="Failure WS", campaign_name="Failure Campaign"
    )
    CampaignBriefRepository(db_session).create(
        campaign_id=campaign.id, version=1, prompt="Idea de prueba para fallo controlado.",
        product_type="Curso online", price=None, audience="Principiantes", budget=None, channel="Instagram",
    )
    db_session.commit()

    service = OrchestrationService(db_session)
    service.initialize_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)
    service.start_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    with patch(
        "app.research.service.ResearchService.record_audience_profile",
        side_effect=RuntimeError("simulated audience failure"),
    ):
        with pytest.raises(RuntimeError, match="simulated audience failure"):
            service.run_deterministic_bootstrap(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    db_session.rollback()
    stages = {
        s.stage.value: s.status
        for s in RunStageExecutionRepository(db_session).list_for_run(campaign_run_id=run.id)
    }
    assert stages["RESEARCH"] == StageExecutionStatus.COMPLETED
    assert stages["AUDIENCE"] == StageExecutionStatus.FAILED
    assert stages["STRATEGY"] == StageExecutionStatus.PENDING
    assert stages["PLAN"] == StageExecutionStatus.PENDING
    assert stages["CONTENT"] == StageExecutionStatus.PENDING

    db_session.refresh(run)
    assert run.status is CampaignRunStatus.RUNNING

    assert db_session.execute(
        select(func.count()).select_from(ResearchReport).where(ResearchReport.campaign_id == campaign.id)
    ).scalar_one() == 1
    assert db_session.execute(
        select(func.count()).select_from(AudienceProfile).where(AudienceProfile.campaign_id == campaign.id)
    ).scalar_one() == 0
    assert db_session.execute(
        select(func.count()).select_from(Strategy).where(Strategy.campaign_id == campaign.id)
    ).scalar_one() == 0


def test_strategy_failure_leaves_research_and_audience_completed(db_session) -> None:
    _org, _ws, campaign, run = _build_campaign_run(
        db_session, org_name="Strategy Failure Org", workspace_name="Strategy Failure WS", campaign_name="Strategy Failure Campaign"
    )
    CampaignBriefRepository(db_session).create(
        campaign_id=campaign.id, version=1, prompt="Otra idea de prueba.",
        product_type=None, price=None, audience=None, budget=None, channel=None,
    )
    db_session.commit()

    service = OrchestrationService(db_session)
    service.initialize_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)
    service.start_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    with patch.object(StrategyService, "record_strategy", side_effect=RuntimeError("simulated strategy failure")):
        with pytest.raises(RuntimeError, match="simulated strategy failure"):
            service.run_deterministic_bootstrap(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    db_session.rollback()
    stages = {
        s.stage.value: s.status
        for s in RunStageExecutionRepository(db_session).list_for_run(campaign_run_id=run.id)
    }
    assert stages["RESEARCH"] == StageExecutionStatus.COMPLETED
    assert stages["AUDIENCE"] == StageExecutionStatus.COMPLETED
    assert stages["STRATEGY"] == StageExecutionStatus.FAILED
    assert stages["PLAN"] == StageExecutionStatus.PENDING

    db_session.refresh(run)
    assert run.status is CampaignRunStatus.RUNNING


def test_failed_stage_has_no_legal_retry_transition(db_session) -> None:
    """Confirms the MVP-04 non-negotiable: FAILED remains terminal —
    no FAILED->READY edge was added."""
    from app.orchestration.transitions import STAGE_TRANSITIONS

    assert STAGE_TRANSITIONS[StageExecutionStatus.FAILED] == frozenset()


# --- tenancy (§31) ------------------------------------------------------------


def test_bootstrap_outputs_are_isolated_per_workspace(auth_client) -> None:
    from fastapi.testclient import TestClient

    csrf_a = register_and_get_csrf(auth_client, display_name="Tenancy A")
    body_a = auth_client.post(
        "/api/v1/campaigns", json=campaign_payload(name="Tenancy Campaign A"), headers={"X-CSRF-Token": csrf_a}
    ).json()
    fixtures_a = {"client": auth_client, "csrf_token": csrf_a, "campaign_id": body_a["campaign"]["id"], "run_id": body_a["run"]["id"]}
    initialize_run(fixtures_a)
    start_run(fixtures_a)

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    csrf_b = register_and_get_csrf(client_b, display_name="Tenancy B")
    body_b = client_b.post(
        "/api/v1/campaigns", json=campaign_payload(name="Tenancy Campaign B"), headers={"X-CSRF-Token": csrf_b}
    ).json()
    fixtures_b = {"client": client_b, "csrf_token": csrf_b, "campaign_id": body_b["campaign"]["id"], "run_id": body_b["run"]["id"]}
    initialize_run(fixtures_b)
    start_run(fixtures_b)

    # B cannot forge access to A's run using A's own client/csrf.
    forged = dict(fixtures_b, client=fixtures_a["client"], csrf_token=fixtures_a["csrf_token"])
    response = forged["client"].get(run_path(forged, "/stages"))
    assert response.status_code == 403

    # A's own bootstrap output belongs only to A's campaign.
    stages_a = fixtures_a["client"].get(run_path(fixtures_a, "/stages")).json()["items"]
    by_stage_a = {s["stage"]: s["status"] for s in stages_a}
    assert by_stage_a["PLAN"] == "COMPLETED"
    assert by_stage_a["CONTENT"] == "COMPLETED"
    stages_b = fixtures_b["client"].get(run_path(fixtures_b, "/stages")).json()["items"]
    by_stage_b = {s["stage"]: s["status"] for s in stages_b}
    assert by_stage_b["PLAN"] == "COMPLETED"
    assert by_stage_b["CONTENT"] == "COMPLETED"
    # Two campaigns, two independent Strategies — never shared/cross-linked.
    assert body_a["campaign"]["id"] != body_b["campaign"]["id"]


# --- audit (§32) ---------------------------------------------------------------


def test_bootstrap_domain_events_are_attributed_to_system_never_agent(campaign_run_client: dict, db_session) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    campaign = CampaignRepository(db_session).get_by_public_id(campaign_run_client["campaign_id"])
    domain_event_types = {
        "research.report.recorded", "audience.profile.recorded",
        "strategy.recorded", "strategy.hypothesis.recorded",
        "planning.plan.recorded", "planning.plan_item.recorded",
    }
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.campaign_id == campaign.id, AuditEvent.event_type.in_(domain_event_types))
    ).scalars().all()

    assert len(events) >= 6  # report + profile + strategy + hypothesis + plan + item

    # MVP-05E: Content events carry no campaign_id (ContentBrief/Piece/
    # Version reach their campaign only by traversal, matching
    # app/content/service.py's own event.record() calls) — scoped by
    # workspace_id instead, safe here since campaign_run_client's fixture
    # registers exactly one fresh user/workspace/campaign per test.
    content_event_types = {"content.brief.recorded", "content.piece.recorded", "content.version.recorded"}
    content_events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.workspace_id == campaign.workspace_id, AuditEvent.event_type.in_(content_event_types)
        )
    ).scalars().all()
    assert len(content_events) == 6  # 2 PlanItems x (1 brief + 1 piece + 1 version)
    for event in content_events:
        assert event.actor_type is ActorType.SYSTEM
        assert event.actor_type is not ActorType.AGENT
    for event in events:
        assert event.actor_type is ActorType.SYSTEM
        assert event.actor_type is not ActorType.AGENT

    report = db_session.execute(select(ResearchReport).where(ResearchReport.campaign_id == campaign.id)).scalar_one()
    report_event = next(e for e in events if e.event_type == "research.report.recorded")
    assert report_event.research_report_id == report.id


# --- Campaign Brief internal read (§33) ---------------------------------------


def test_get_latest_for_campaign_returns_the_correct_brief(db_session) -> None:
    _org, _ws, campaign, _run = _build_campaign_run(
        db_session, org_name="Brief Org", workspace_name="Brief WS", campaign_name="Brief Campaign"
    )
    repo = CampaignBriefRepository(db_session)
    repo.create(
        campaign_id=campaign.id, version=1, prompt="Primera version.",
        product_type=None, price=None, audience=None, budget=None, channel=None,
    )
    latest = repo.create(
        campaign_id=campaign.id, version=2, prompt="Segunda version (la actual).",
        product_type="Ebook", price="19 USD", audience="Emprendedores", budget="100 USD", channel="Email",
    )
    db_session.commit()

    result = repo.get_latest_for_campaign(campaign.id)
    assert result is not None
    assert result.id == latest.id
    assert result.prompt == "Segunda version (la actual)."


def test_get_latest_for_campaign_never_returns_another_campaigns_brief(db_session) -> None:
    _org_a, _ws_a, campaign_a, _run_a = _build_campaign_run(
        db_session, org_name="Brief Iso Org A", workspace_name="Brief Iso WS A", campaign_name="Brief Iso Campaign A"
    )
    _org_b, _ws_b, campaign_b, _run_b = _build_campaign_run(
        db_session, org_name="Brief Iso Org B", workspace_name="Brief Iso WS B", campaign_name="Brief Iso Campaign B"
    )
    repo = CampaignBriefRepository(db_session)
    brief_a = repo.create(
        campaign_id=campaign_a.id, version=1, prompt="Brief de A.",
        product_type=None, price=None, audience=None, budget=None, channel=None,
    )
    repo.create(
        campaign_id=campaign_b.id, version=1, prompt="Brief de B.",
        product_type=None, price=None, audience=None, budget=None, channel=None,
    )
    db_session.commit()

    result = repo.get_latest_for_campaign(campaign_a.id)
    assert result.id == brief_a.id
    assert result.prompt == "Brief de A."


def test_get_latest_for_campaign_returns_none_when_no_brief_exists(db_session) -> None:
    _org, _ws, campaign, _run = _build_campaign_run(
        db_session, org_name="No Brief Org", workspace_name="No Brief WS", campaign_name="No Brief Campaign"
    )
    db_session.commit()
    assert CampaignBriefRepository(db_session).get_latest_for_campaign(campaign.id) is None
