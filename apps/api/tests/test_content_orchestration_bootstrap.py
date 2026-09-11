"""MVP-05E: CONTENT orchestration bootstrap extension (PLAN COMPLETED ->
CONTENT COMPLETED). All marked `postgres`.

Complements ``tests/test_orchestration_bootstrap.py`` (which already covers
the RESEARCH -> AUDIENCE -> STRATEGY -> PLAN -> CONTENT stage-status matrix,
row counts, DRAFT status, zero-approval, and audit-event attribution for the
success path) with the scenarios specific to Content's own materialization
logic: persisted-upstream sourcing, idempotency/defense-in-depth on a second
internal invocation, partial mid-stage failure, Asset/Learning isolation,
and GET readback through the existing (unchanged) public Content routes.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.assets.models import Asset, AssetVersion, CreativeBrief
from app.campaigns.models import CampaignRunStatus
from app.campaigns.repository import CampaignBriefRepository, CampaignRepository, CampaignRunRepository
from app.content.models import ContentApproval, ContentBrief, ContentPiece, ContentPieceStatus, ContentVersion
from app.core.api_errors import InvalidLifecycleTransitionError
from app.learning.models import LearningCandidate, StrategicRecommendationCandidate
from app.orchestration import bootstrap as bootstrap_module
from app.orchestration.models import StageExecutionStatus
from app.orchestration.repository import RunStageExecutionRepository
from app.orchestration.service import OrchestrationService
from app.planning.models import ContentPlan, PlanItem
from app.strategy.models import Positioning, Strategy
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository
from tests.orchestrationtest import initialize_run, start_run

pytestmark = pytest.mark.postgres


def _build_campaign_run(session, *, org_name: str, workspace_name: str, campaign_name: str):
    organization = OrganizationRepository(session).create(name=org_name)
    workspace = WorkspaceRepository(session).create(organization_id=organization.id, name=workspace_name)
    campaign = CampaignRepository(session).create(workspace_id=workspace.id, name=campaign_name)
    run = CampaignRunRepository(session).create(campaign=campaign, run_number=1)
    session.flush()
    return organization, workspace, campaign, run


def _content_rows_for_plan(db_session, plan):
    briefs = db_session.execute(select(ContentBrief).where(ContentBrief.content_plan_id == plan.id)).scalars().all()
    pieces = db_session.execute(
        select(ContentPiece).where(ContentPiece.content_brief_id.in_([b.id for b in briefs]))
    ).scalars().all() if briefs else []
    versions = db_session.execute(
        select(ContentVersion).where(ContentVersion.content_piece_id.in_([p.id for p in pieces]))
    ).scalars().all() if pieces else []
    return briefs, pieces, versions


# --- 1/9. success path uses persisted upstream data (§9 PERSISTED-UPSTREAM) --


def test_content_materializes_from_persisted_plan_and_positioning(campaign_run_client: dict, db_session) -> None:
    """Proves Content genuinely reads persisted PLAN/STRATEGY output rather
    than any in-memory value: the Positioning statement is synthesized text
    unique to Strategy's own output, and PlanItem.objective/format/sequence
    are synthesized text unique to Planning's own output — their presence,
    verbatim, in the persisted ContentBrief/ContentVersion is only possible
    if Content genuinely re-read both from persistence (mirrors the
    MVP-04R Strategy->Planning provenance test's own method)."""
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    campaign = CampaignRepository(db_session).get_by_public_id(campaign_run_client["campaign_id"])
    strategy = db_session.execute(select(Strategy).where(Strategy.campaign_id == campaign.id)).scalar_one()
    positioning = db_session.execute(select(Positioning).where(Positioning.strategy_id == strategy.id)).scalar_one()
    plan = db_session.execute(select(ContentPlan).where(ContentPlan.campaign_id == campaign.id)).scalar_one()
    plan_items = db_session.execute(
        select(PlanItem).where(PlanItem.content_plan_id == plan.id).order_by(PlanItem.sequence.asc())
    ).scalars().all()
    assert len(plan_items) == 2

    briefs, pieces, versions = _content_rows_for_plan(db_session, plan)
    assert len(briefs) == 2 and len(pieces) == 2 and len(versions) == 2

    briefs_by_item = {b.plan_item_id: b for b in briefs}
    pieces_by_brief = {p.content_brief_id: p for p in pieces}
    versions_by_piece = {v.content_piece_id: v for v in versions}

    for plan_item in plan_items:
        brief = briefs_by_item[plan_item.id]
        assert positioning.statement in brief.brief
        assert plan_item.objective in brief.brief
        assert str(plan_item.sequence) in brief.brief

        piece = pieces_by_brief[brief.id]
        assert piece.format == plan_item.format
        assert piece.objective == plan_item.objective
        assert piece.status is ContentPieceStatus.DRAFT

        version = versions_by_piece[piece.id]
        assert version.payload["objective"] == plan_item.objective
        assert version.payload["positioning_reference"] == positioning.statement
        assert version.payload["sequence"] == plan_item.sequence


# --- 2. Content stage becomes COMPLETED only after real persistence --------


def test_plan_failure_leaves_content_pending_never_completed_on_no_persistence(db_session) -> None:
    """CONTENT must never be marked COMPLETED without real persisted rows
    behind it — if PLAN itself fails, CONTENT must never even begin."""
    _org, _ws, campaign, run = _build_campaign_run(
        db_session, org_name="Content Plan-Fail Org", workspace_name="Content Plan-Fail WS",
        campaign_name="Content Plan-Fail Campaign",
    )
    CampaignBriefRepository(db_session).create(
        campaign_id=campaign.id, version=1, prompt="Idea sin plan disponible.",
        product_type=None, price=None, audience=None, budget=None, channel=None,
    )
    db_session.commit()

    service = OrchestrationService(db_session)
    service.initialize_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)
    service.start_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    from app.planning.service import PlanningService

    with patch.object(PlanningService, "record_plan", side_effect=RuntimeError("simulated plan failure")):
        with pytest.raises(RuntimeError, match="simulated plan failure"):
            service.run_deterministic_bootstrap(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    db_session.rollback()
    stages = {s.stage.value: s.status for s in RunStageExecutionRepository(db_session).list_for_run(campaign_run_id=run.id)}
    assert stages["PLAN"] == StageExecutionStatus.FAILED
    assert stages["CONTENT"] == StageExecutionStatus.PENDING

    assert db_session.execute(
        select(func.count()).select_from(ContentPlan).where(ContentPlan.campaign_id == campaign.id)
    ).scalar_one() == 0


# --- 2b. zero-PlanItem edge case (MVP-05E-R §12): a persisted Plan with ----
# --- zero items is a pre-existing, explicitly supported PlanningService ---
# --- state (record_plan accepts items=None/[] and creates zero PlanItem ---
# --- rows without raising) — CONTENT faithfully mirrors zero inputs into --
# --- zero outputs rather than treating "nothing to materialize" as a ------
# --- failure. Nothing in this bootstrap path failed; there was simply -----
# --- nothing for it to do. -------------------------------------------------


def test_zero_plan_items_completes_content_with_zero_content_rows(db_session) -> None:
    """OPTION A (VALID EMPTY CONTENT COMPLETION), decided against the
    actual PlanningService contract, not by convenience: ``record_plan``'s
    own ``items = items or []`` / ``create_many(...) if items else []``
    already treats a zero-item Plan as a normal, non-error, successfully
    persisted output (BACKEND-09's own docstring: "atomic *initial* write:
    Content Plan + any initial Plan Items"; a Plan with none is still a
    real persisted ContentPlan). CONTENT's own materialization loop is
    "one Brief+Piece+Version per persisted PlanItem" — with zero PlanItems
    persisted, that rule is honestly satisfied by producing zero Content
    rows, not by fabricating placeholder content or by raising an
    artificial failure for an input state the upstream domain itself
    never treats as an error."""
    _org, _ws, campaign, run = _build_campaign_run(
        db_session, org_name="Content Zero-Item Org", workspace_name="Content Zero-Item WS",
        campaign_name="Content Zero-Item Campaign",
    )
    CampaignBriefRepository(db_session).create(
        campaign_id=campaign.id, version=1, prompt="Idea de prueba sin elementos de plan.",
        product_type="Curso online", price=None, audience="Principiantes", budget=None, channel="Instagram",
    )
    db_session.commit()

    service = OrchestrationService(db_session)
    service.initialize_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)
    service.start_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    real_build_plan_content = bootstrap_module.build_plan_content

    def zero_item_plan_content(*, campaign_name, brief, strategy_positioning):
        content = real_build_plan_content(
            campaign_name=campaign_name, brief=brief, strategy_positioning=strategy_positioning
        )
        return {"summary": content["summary"], "items": []}

    with patch.object(bootstrap_module, "build_plan_content", side_effect=zero_item_plan_content):
        service.run_deterministic_bootstrap(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    stages = {s.stage.value: s.status for s in RunStageExecutionRepository(db_session).list_for_run(campaign_run_id=run.id)}
    assert stages["RESEARCH"] == StageExecutionStatus.COMPLETED
    assert stages["AUDIENCE"] == StageExecutionStatus.COMPLETED
    assert stages["STRATEGY"] == StageExecutionStatus.COMPLETED
    assert stages["PLAN"] == StageExecutionStatus.COMPLETED
    assert stages["CONTENT"] == StageExecutionStatus.COMPLETED
    assert stages["CREATIVE"] == StageExecutionStatus.PENDING

    plan = db_session.execute(select(ContentPlan).where(ContentPlan.campaign_id == campaign.id)).scalar_one()
    plan_items = db_session.execute(select(PlanItem).where(PlanItem.content_plan_id == plan.id)).scalars().all()
    assert plan_items == []

    briefs, pieces, versions = _content_rows_for_plan(db_session, plan)
    assert briefs == [] and pieces == [] and versions == []
    assert db_session.execute(
        select(func.count())
        .select_from(ContentApproval)
        .join(ContentVersion, ContentApproval.content_version_id == ContentVersion.id)
        .join(ContentPiece, ContentVersion.content_piece_id == ContentPiece.id)
        .join(ContentBrief, ContentPiece.content_brief_id == ContentBrief.id)
        .where(ContentBrief.content_plan_id == plan.id)
    ).scalar_one() == 0

    db_session.refresh(run)
    assert run.status is CampaignRunStatus.RUNNING


# --- 3. partial Content failure: prior stages survive, Content FAILED, ------
# --- later stages PENDING, prior committed Content rows are not undone -----


def test_partial_content_failure_leaves_first_item_materialized_and_marks_content_failed(db_session) -> None:
    """Simulates a genuine mid-stage failure on the second of two PlanItems
    (build_content_version_payload raises for sequence==2, but the real
    builder still runs for sequence==1). Proves the exact atomicity model:
    each ContentService write commits independently, so PlanItem #1's
    Brief+Piece+Version survive even though the CONTENT stage as a whole is
    marked FAILED and PlanItem #2 is left with a Brief but no Piece. Calls
    the internal service method directly (bypassing the HTTP `/start`
    route) to observe the raised exception directly, the same pattern
    ``test_audience_failure_leaves_research_completed_and_stops_the_
    bootstrap``/``test_strategy_failure_leaves_research_and_audience_
    completed`` already use in ``tests/test_orchestration_bootstrap.py``."""
    _org, _ws, campaign, run = _build_campaign_run(
        db_session, org_name="Content Failure Org", workspace_name="Content Failure WS",
        campaign_name="Content Failure Campaign",
    )
    CampaignBriefRepository(db_session).create(
        campaign_id=campaign.id, version=1, prompt="Idea de prueba para fallo de contenido.",
        product_type="Curso online", price=None, audience="Principiantes", budget=None, channel="Instagram",
    )
    db_session.commit()

    service = OrchestrationService(db_session)
    service.initialize_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)
    service.start_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    real_build_payload = bootstrap_module.build_content_version_payload

    def flaky_build_payload(*, plan_item, strategy_positioning):
        if plan_item.sequence == 2:
            raise RuntimeError("simulated content failure")
        return real_build_payload(plan_item=plan_item, strategy_positioning=strategy_positioning)

    with patch.object(bootstrap_module, "build_content_version_payload", side_effect=flaky_build_payload):
        with pytest.raises(RuntimeError, match="simulated content failure"):
            service.run_deterministic_bootstrap(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    db_session.rollback()
    stages = {s.stage.value: s.status for s in RunStageExecutionRepository(db_session).list_for_run(campaign_run_id=run.id)}
    assert stages["RESEARCH"] == StageExecutionStatus.COMPLETED
    assert stages["AUDIENCE"] == StageExecutionStatus.COMPLETED
    assert stages["STRATEGY"] == StageExecutionStatus.COMPLETED
    assert stages["PLAN"] == StageExecutionStatus.COMPLETED
    assert stages["CONTENT"] == StageExecutionStatus.FAILED
    assert stages["CREATIVE"] == StageExecutionStatus.PENDING

    db_session.refresh(campaign)
    plan = db_session.execute(select(ContentPlan).where(ContentPlan.campaign_id == campaign.id)).scalar_one()
    plan_items = {
        item.sequence: item
        for item in db_session.execute(select(PlanItem).where(PlanItem.content_plan_id == plan.id)).scalars().all()
    }
    briefs, pieces, versions = _content_rows_for_plan(db_session, plan)
    assert len(briefs) == 2  # both Briefs were created before the fault
    assert len(pieces) == 1  # only PlanItem #1's Piece committed
    assert len(versions) == 1

    committed_piece = pieces[0]
    briefs_by_item = {b.plan_item_id: b for b in briefs}
    assert committed_piece.content_brief_id == briefs_by_item[plan_items[1].id].id

    # The Run itself stays RUNNING — a partial bootstrap failure is not a
    # terminal Run state (matches every other stage's own precedent).
    db_session.refresh(run)
    assert run.status is CampaignRunStatus.RUNNING


# --- 4. idempotency / defense-in-depth --------------------------------------


def test_second_internal_bootstrap_invocation_creates_no_duplicate_content_rows(
    campaign_run_client: dict, db_session,
) -> None:
    """The public START endpoint already rejects a second call with 409
    (test_second_start_is_rejected_and_creates_no_duplicate_output), but
    defense-in-depth means the internal service method itself must also be
    safe if ever invoked a second time directly. Every stage is already
    COMPLETED after the first run, so `_run_bootstrap_stage`'s own
    COMPLETED-guard makes the second call a full no-op for every stage,
    Content included."""
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    campaign = CampaignRepository(db_session).get_by_public_id(campaign_run_client["campaign_id"])
    run = CampaignRunRepository(db_session).get_by_public_id(campaign_run_client["run_id"])
    plan = db_session.execute(select(ContentPlan).where(ContentPlan.campaign_id == campaign.id)).scalar_one()
    briefs_before, pieces_before, versions_before = _content_rows_for_plan(db_session, plan)
    assert len(briefs_before) == 2 and len(pieces_before) == 2 and len(versions_before) == 2

    service = OrchestrationService(db_session)
    service.run_deterministic_bootstrap(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    briefs_after, pieces_after, versions_after = _content_rows_for_plan(db_session, plan)
    assert len(briefs_after) == 2 and len(pieces_after) == 2 and len(versions_after) == 2
    assert {b.id for b in briefs_after} == {b.id for b in briefs_before}
    assert {p.id for p in pieces_after} == {p.id for p in pieces_before}
    assert {v.id for v in versions_after} == {v.id for v in versions_before}


def test_content_retry_reuses_existing_brief_and_never_duplicates_it(db_session) -> None:
    """Continues from the same partial-failure shape as the test above
    (PlanItem #2 has a Brief but no Piece) and re-invokes the internal
    bootstrap method directly. Proves the finer-grained idempotency check
    (get_brief_for_plan_item / get_piece_for_brief) reuses the existing
    Brief and materializes only the missing Piece+Version — never a second
    Brief. Because CONTENT's own stage_execution is left FAILED (a terminal
    status with no legal outgoing edge — see
    test_failed_stage_has_no_legal_retry_transition), the retry's own
    stage-level COMPLETED transition is correctly rejected even though the
    row-level materialization it attempted succeeds; this is the existing,
    approved FAILED-is-terminal invariant, not a defect introduced here."""
    _org, _ws, campaign, run = _build_campaign_run(
        db_session, org_name="Content Retry Org", workspace_name="Content Retry WS",
        campaign_name="Content Retry Campaign",
    )
    CampaignBriefRepository(db_session).create(
        campaign_id=campaign.id, version=1, prompt="Idea de prueba para reintento de contenido.",
        product_type="Curso online", price=None, audience="Principiantes", budget=None, channel="Instagram",
    )
    db_session.commit()

    service = OrchestrationService(db_session)
    service.initialize_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)
    service.start_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    real_build_payload = bootstrap_module.build_content_version_payload

    def flaky_build_payload(*, plan_item, strategy_positioning):
        if plan_item.sequence == 2:
            raise RuntimeError("simulated content failure")
        return real_build_payload(plan_item=plan_item, strategy_positioning=strategy_positioning)

    with patch.object(bootstrap_module, "build_content_version_payload", side_effect=flaky_build_payload):
        with pytest.raises(RuntimeError, match="simulated content failure"):
            service.run_deterministic_bootstrap(campaign=campaign, run=run, actor_user_id=None, request_id=None)
    db_session.rollback()

    plan = db_session.execute(select(ContentPlan).where(ContentPlan.campaign_id == campaign.id)).scalar_one()
    briefs_before, pieces_before, _versions_before = _content_rows_for_plan(db_session, plan)
    assert len(briefs_before) == 2
    assert len(pieces_before) == 1

    with pytest.raises(InvalidLifecycleTransitionError):
        service.run_deterministic_bootstrap(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    db_session.rollback()
    briefs_after, pieces_after, versions_after = _content_rows_for_plan(db_session, plan)
    assert len(briefs_after) == 2
    assert {b.id for b in briefs_after} == {b.id for b in briefs_before}  # never duplicated
    assert len(pieces_after) == 2  # the missing Piece was materialized
    assert len(versions_after) == 2

    stages = {s.stage.value: s.status for s in RunStageExecutionRepository(db_session).list_for_run(campaign_run_id=run.id)}
    assert stages["CONTENT"] == StageExecutionStatus.FAILED


# --- 5. Asset/Creative/Learning/StrategicRecommendation isolation ----------


def test_bootstrap_creates_zero_asset_and_learning_rows(campaign_run_client: dict, db_session) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    campaign = CampaignRepository(db_session).get_by_public_id(campaign_run_client["campaign_id"])
    plan = db_session.execute(select(ContentPlan).where(ContentPlan.campaign_id == campaign.id)).scalar_one()
    _briefs, pieces, _versions = _content_rows_for_plan(db_session, plan)
    piece_ids = [p.id for p in pieces]

    assert db_session.execute(
        select(func.count()).select_from(CreativeBrief).where(CreativeBrief.content_piece_id.in_(piece_ids))
    ).scalar_one() == 0
    # Scoped to this test's own workspace (Asset.workspace_id is a direct
    # column) — a table-wide SELECT would pick up Asset/AssetVersion rows
    # committed by unrelated Asset-domain tests sharing the same Postgres
    # database across the full suite run, the same leakage
    # `_content_rows_for_plan`'s own campaign/plan scoping already avoids.
    workspace_asset_ids = db_session.execute(
        select(Asset.id).where(Asset.workspace_id == campaign.workspace_id)
    ).scalars().all()
    assert len(workspace_asset_ids) == 0
    assert db_session.execute(
        select(func.count()).select_from(AssetVersion).where(AssetVersion.asset_id.in_(workspace_asset_ids or [None]))
    ).scalar_one() == 0

    assert db_session.execute(
        select(func.count()).select_from(LearningCandidate).where(LearningCandidate.workspace_id == campaign.workspace_id)
    ).scalar_one() == 0
    assert db_session.execute(
        select(func.count())
        .select_from(StrategicRecommendationCandidate)
        .where(StrategicRecommendationCandidate.workspace_id == campaign.workspace_id)
    ).scalar_one() == 0


# --- 6. public Content GET routes can read the generated rows --------------


def test_content_list_and_detail_can_read_bootstrap_generated_rows(campaign_run_client: dict) -> None:
    """MVP-05E §29/§AA: no new public endpoint is added — the two existing
    GET routes from BACKEND-10/MVP-05D must simply now return real rows
    once the bootstrap has run."""
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    client = campaign_run_client["client"]
    campaign_id = campaign_run_client["campaign_id"]

    list_response = client.get(f"/api/v1/campaigns/{campaign_id}/content")
    assert list_response.status_code == 200
    items = list_response.json()["items"]
    assert len(items) == 2
    for item in items:
        assert item["status"] == "DRAFT"
        assert "id" in item and item["id"]
        # Non-leaky: only public_id-shaped strings, no raw UUID/workspace_id.
        assert "workspace_id" not in item

    detail_response = client.get(f"/api/v1/campaigns/{campaign_id}/content/{items[0]['id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["piece"]["id"] == items[0]["id"]
    assert detail["latest_version"] is not None
    assert detail["latest_version"]["payload"]["kind"] == "draft"
    assert "disclaimer" in detail["latest_version"]["payload"]
