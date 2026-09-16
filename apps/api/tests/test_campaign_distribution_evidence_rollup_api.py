"""Campaign Distribution Evidence Rollup HTTP contract (MVP-22, frozen by
MVP-22A); real PostgreSQL only.

Reuses the Evidence fixtures/helpers already established in
``tests/test_distribution_evidence_api.py`` (which itself reuses
``tests/test_distribution_api.py`` and ``tests/test_content_api.py``) to
reach several independent, genuinely DISTRIBUTED ContentPieces within one
Campaign before exercising the rollup.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.content.models import ContentDistribution, ContentVersion
from app.content.service import ContentService
from app.measurement.models import DistributionMetricEvidence, MetricEntry, MetricValue
from app.measurement.repository import DistributionMetricEvidenceRepository
from app.measurement.service import MeasurementService
from app.persistence.session import get_engine
from app.workspaces.models import MembershipRole
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.measurementtest import next_client_request_id
from tests.settingstest import add_member_to_workspace, login_as
from tests.test_content_api import _record_content_piece
from tests.test_distribution_api import campaign_client_with_stages  # noqa: F401 (fixture)
from tests.test_distribution_evidence_api import (
    _correct_evidence,
    _create_evidence,
    _distributed,
    _distributed_piece_in_session,
    _today,
    _total_count,
)
from app.content.models import ContentApprovalStatus
from app.orchestration.models import BusinessStage
from app.planning.service import PlanningService
from tests.contenttest import build_plan_with_item, default_piece_fields, default_version_payload, make_user
from tests.planningtest import default_plan_item
from tests.researchtest import build_campaign_run_with_stages

pytestmark = pytest.mark.postgres


def _distribute_piece_from_item(session, *, campaign, item, plan, user):
    """Shared tail of ``_distributed_piece_in_session`` (test_distribution_
    evidence_api.py), parameterized on an already-built PlanItem so a
    second, independent Piece/Distribution can be added to an EXISTING
    Campaign rather than always creating a brand-new one."""
    service = ContentService(session)
    brief = service.record_brief(plan_item=item, content_plan=plan, brief="Campaign rollup race")
    piece, _version = service.record_piece(content_brief=brief, initial_payload=default_version_payload(), **default_piece_fields())
    for transition in (service.mark_in_production, service.mark_produced, service.mark_ready_for_review):
        transition(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id, actor_user_id=user.id)
    approval = service.request_approval(
        workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id, actor_user_id=user.id
    )
    service.mark_under_review(workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id, actor_user_id=user.id)
    service.record_authorized_approval_decision(
        workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id,
        decision=ContentApprovalStatus.APPROVED, actor_user_id=user.id,
    )
    service.mark_ready_for_distribution(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id, actor_user_id=user.id)
    service.record_distributed(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id, actor_user_id=user.id)
    return session.execute(
        select(ContentDistribution).where(ContentDistribution.content_piece_id == piece.id)
    ).scalar_one()


def _two_distributed_pieces_in_same_campaign(session):
    """MVP-22A §69: builds ONE Campaign with TWO independent, genuinely
    DISTRIBUTED Pieces — needed for domain-level cross-Distribution tests
    that ``_distributed_piece_in_session`` alone cannot produce, since it
    always creates a brand-new Campaign per call."""
    campaign, run, stages, plan_a, item_a = build_plan_with_item(session)
    user = make_user(session)
    distribution_a = _distribute_piece_from_item(session, campaign=campaign, item=item_a, plan=plan_a, user=user)

    plan_b, items_b = PlanningService(session).record_plan(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN],
        summary="A second content calendar.", items=[default_plan_item()],
    )
    distribution_b = _distribute_piece_from_item(session, campaign=campaign, item=items_b[0], plan=plan_b, user=user)
    return campaign, distribution_a, distribution_b, user


def _rollup_path(fixtures) -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/distribution/evidence/summary"


def _rollup(fixtures):
    return fixtures["client"].get(_rollup_path(fixtures))


def _metric(body: dict, name: str) -> dict:
    return next(m for m in body["metrics"] if m["metric_name"] == name)


# --- empty / zero-evidence campaigns ----------------------------------------------


def test_campaign_with_zero_distributions_returns_empty_metrics(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    _record_content_piece(fixtures)  # a Piece exists, but is never distributed

    response = _rollup(fixtures)
    assert response.status_code == 200, response.text
    assert response.json() == {"campaign_id": fixtures["campaign_id"], "metrics": []}


def test_campaign_with_distributions_but_zero_evidence_returns_empty_metrics(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    _distributed(fixtures)

    response = _rollup(fixtures)
    assert response.status_code == 200, response.text
    assert response.json() == {"campaign_id": fixtures["campaign_id"], "metrics": []}


# --- correction chains / report count ----------------------------------------------


def test_correction_chains_across_multiple_distributions(campaign_client_with_stages):
    """Distribution A: E1 -> E2 -> E3 (only E3 current). Distribution B: E4
    independent. Distribution C: E5 -> E6 (only E6 current). Expected
    Campaign-wide membership: E3, E4, E6 only."""
    fixtures = campaign_client_with_stages
    content_a = _distributed(fixtures)
    e1 = _create_evidence(fixtures, content_a, values={"reach": 100}).json()
    e2 = _correct_evidence(fixtures, content_a, e1["id"], values={"reach": 200}).json()
    _correct_evidence(fixtures, content_a, e2["id"], values={"reach": 300})

    content_b = _distributed(fixtures)
    _create_evidence(fixtures, content_b, values={"reach": 999})

    content_c = _distributed(fixtures)
    e5 = _create_evidence(fixtures, content_c, values={"reach": 1}).json()
    _correct_evidence(fixtures, content_c, e5["id"], values={"reach": 2})

    body = _rollup(fixtures).json()
    reach = _metric(body, "reach")
    assert reach["report_count"] == 3, "[CORRECTION] only the terminal row of each lineage must contribute"
    values_present = {reach["latest"]["value"], reach["earliest"]["value"]}
    assert "100.0000" not in values_present and "200.0000" not in values_present, "[CORRECTION] superseded E1/E2 must never win latest/earliest"
    assert "1.0000" not in values_present, "[CORRECTION] superseded E5 must never win latest/earliest"


def test_report_count_independent_reports_same_and_different_distributions(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    today = _today()
    content_a = _distributed(fixtures)
    _create_evidence(
        fixtures, content_a, values={"reach": 10},
        period_start=(today - timedelta(days=5)).isoformat(), period_end=today.isoformat(),
        client_request_id=next_client_request_id(),
    )
    _create_evidence(
        fixtures, content_a, values={"reach": 20},
        period_start=(today - timedelta(days=5)).isoformat(), period_end=today.isoformat(),
        client_request_id=next_client_request_id(),
    )
    content_b = _distributed(fixtures)
    _create_evidence(
        fixtures, content_b, values={"reach": 30},
        period_start=(today - timedelta(days=20)).isoformat(), period_end=today.isoformat(),
        client_request_id=next_client_request_id(),
    )

    body = _rollup(fixtures).json()
    reach = _metric(body, "reach")
    assert reach["report_count"] == 3, "[INDEPENDENT REPORTS] same-Distribution and cross-Distribution independent reports all count"


# --- multi-metric / case sensitivity -----------------------------------------------


def test_multi_metric_case_sensitivity_no_normalization(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_a = _distributed(fixtures)
    _create_evidence(fixtures, content_a, values={"Clicks": 5, "clicks": 15})

    body = _rollup(fixtures).json()
    names = {m["metric_name"] for m in body["metrics"]}
    assert names == {"Clicks", "clicks"}, "[NO NORMALIZATION] distinct stored identities must remain distinct"


# --- latest / earliest + provenance -------------------------------------------------


def test_latest_earliest_provenance_across_distributions(campaign_client_with_stages):
    """Latest and earliest for the same metric come from two DIFFERENT
    Distributions/Pieces — the response must correctly attribute each to
    its real source, not merely produce a numerically-correct value."""
    fixtures = campaign_client_with_stages
    today = _today()
    content_a = _distributed(fixtures)
    _create_evidence(
        fixtures, content_a, values={"reach": 111},
        period_start=(today - timedelta(days=5)).isoformat(), period_end=today.isoformat(),
        client_request_id=next_client_request_id(),
    )
    content_b = _distributed(fixtures)
    _create_evidence(
        fixtures, content_b, values={"reach": 222},
        period_start=(today - timedelta(days=20)).isoformat(), period_end=today.isoformat(),
        client_request_id=next_client_request_id(),
    )

    detail_a = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_a}").json()
    detail_b = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_b}").json()

    body = _rollup(fixtures).json()
    reach = _metric(body, "reach")
    assert reach["earliest"]["value"] == "111.0000"
    assert reach["earliest"]["content_piece_id"] == content_a
    assert reach["earliest"]["distribution_id"] == detail_a["distribution"]["id"]
    assert reach["earliest"]["content_version_id"] == detail_a["latest_version"]["id"]
    assert reach["earliest"]["channel"] == detail_a["distribution"]["channel"]

    assert reach["latest"]["value"] == "222.0000"
    assert reach["latest"]["content_piece_id"] == content_b
    assert reach["latest"]["distribution_id"] == detail_b["distribution"]["id"]
    assert reach["latest"]["content_version_id"] == detail_b["latest_version"]["id"]
    assert reach["latest"]["channel"] == detail_b["distribution"]["channel"]

    # No internal UUID anywhere in the response.
    import re

    assert not re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", _rollup(fixtures).text)


def test_period_does_not_determine_latest_earliest_across_distributions(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    today = _today()
    content_a = _distributed(fixtures)
    _create_evidence(
        fixtures, content_a, values={"reach": 900},
        period_start=(today - timedelta(days=5)).isoformat(), period_end=today.isoformat(),
        client_request_id=next_client_request_id(),
    )
    content_b = _distributed(fixtures)
    _create_evidence(
        fixtures, content_b, values={"reach": 100},
        period_start=(today - timedelta(days=60)).isoformat(), period_end=today.isoformat(),
        client_request_id=next_client_request_id(),
    )

    body = _rollup(fixtures).json()
    reach = _metric(body, "reach")
    assert reach["latest"]["value"] == "100.0000", "[REPORTING CHRONOLOGY] the second POST is latest regardless of its older-looking period"


# --- schema boundary / arithmetic ----------------------------------------------------


def test_no_arithmetic_fields_in_response(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_a = _distributed(fixtures)
    _create_evidence(fixtures, content_a, values={"performance": 42})  # a user-named metric, not a derived field

    body = _rollup(fixtures).json()
    metric = _metric(body, "performance")
    assert set(metric.keys()) == {"metric_name", "report_count", "latest", "earliest"}
    for key in ("sum", "average", "mean", "min_value", "max_value", "delta", "percent_change", "trend", "growth", "winner"):
        assert key not in metric, f"[NO ARITHMETIC] forbidden field '{key}' must never appear"
    assert set(metric["latest"].keys()) == {
        "value", "period_start", "period_end", "reported_at", "content_piece_id", "distribution_id",
        "content_version_id", "channel",
    }


# --- tenancy / authorization ---------------------------------------------------------


def test_rollup_open_to_any_active_member_not_just_owner(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    _distributed(fixtures)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}
    response = _rollup(member_fixtures)
    assert response.status_code == 200, response.text


def test_rollup_other_workspace_cannot_read(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_a = _distributed(fixtures)
    _create_evidence(fixtures, content_a)

    outsider = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    register_and_get_csrf(outsider, display_name="Other workspace rollup reader")
    response = outsider.get(_rollup_path(fixtures))
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_rollup_same_workspace_different_campaign_no_leakage(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_a = _distributed(fixtures)
    _create_evidence(fixtures, content_a, values={"reach": 555})

    csrf_b = fixtures["client"].get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = fixtures["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name="Campaign B (same workspace)"), headers={"X-CSRF-Token": csrf_b}
    ).json()
    other_campaign_id = body_b["campaign"]["id"]

    response = fixtures["client"].get(f"/api/v1/campaigns/{other_campaign_id}/distribution/evidence/summary")
    assert response.status_code == 200, response.text
    assert response.json() == {"campaign_id": other_campaign_id, "metrics": []}, "[TENANCY] a sibling Campaign must never see this Campaign's Evidence"


def test_rollup_unknown_campaign_is_non_leaky(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    _distributed(fixtures)
    response = fixtures["client"].get("/api/v1/campaigns/CMP-UNKNOWNUNKNOWN/distribution/evidence/summary")
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_rollup_get_does_not_require_csrf(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    _distributed(fixtures)
    assert _rollup(fixtures).status_code == 200


# --- read-only / analysis isolation --------------------------------------------------


def test_rollup_reads_have_zero_write_side_effects(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_a = _distributed(fixtures)
    _create_evidence(fixtures, content_a)

    def _counts():
        with Session(get_engine()) as session:
            return {
                "audit": session.execute(select(func.count()).select_from(AuditEvent)).scalar_one(),
                "entry": session.execute(select(func.count()).select_from(MetricEntry)).scalar_one(),
                "value": session.execute(select(func.count()).select_from(MetricValue)).scalar_one(),
                "evidence": session.execute(select(func.count()).select_from(DistributionMetricEvidence)).scalar_one(),
            }

    before = _counts()
    for _ in range(5):
        assert _rollup(fixtures).status_code == 200
    after = _counts()
    assert before == after, "[READ-ONLY] repeated rollup GETs must never write AuditEvent/MetricEntry/MetricValue/Evidence rows"


def test_rollup_does_not_alter_aggregate_metrics_or_analysis(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    campaign_id = fixtures["campaign_id"]
    csrf_headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    content_a = _distributed(fixtures)
    _create_evidence(fixtures, content_a)

    aggregate = fixtures["client"].post(
        f"/api/v1/campaigns/{campaign_id}/metrics",
        json={
            "period_start": "2025-12-01", "period_end": "2025-12-31", "channel": "Instagram",
            "source": "MANUAL", "client_request_id": next_client_request_id(), "values": {"clicks": 100},
        },
        headers=csrf_headers,
    )
    assert aggregate.status_code == 201, aggregate.text

    metrics_before = fixtures["client"].get(f"/api/v1/campaigns/{campaign_id}/metrics").json()
    for _ in range(3):
        assert _rollup(fixtures).status_code == 200
    metrics_after = fixtures["client"].get(f"/api/v1/campaigns/{campaign_id}/metrics").json()
    assert metrics_before == metrics_after, "[ANALYSIS ISOLATION] rollup reads must not alter the aggregate /metrics listing"

    run_response = fixtures["client"].post(
        f"/api/v1/campaigns/{campaign_id}/analysis/run", json={"client_request_id": next_client_request_id()}, headers=csrf_headers
    )
    assert run_response.status_code == 200, run_response.text
    analysis_before = fixtures["client"].get(f"/api/v1/campaigns/{campaign_id}/analysis").json()
    for _ in range(3):
        assert _rollup(fixtures).status_code == 200
    analysis_after = fixtures["client"].get(f"/api/v1/campaigns/{campaign_id}/analysis").json()
    assert analysis_before == analysis_after, "[ANALYSIS ISOLATION] rollup reads must not alter analysis results"


# --- domain-level: single-statement / provenance / query-count proofs (direct DB) ---


def test_current_evidence_membership_is_a_single_select_statement(db_session) -> None:
    """MVP-22A §62: prove list_current_for_campaign resolves Campaign-wide
    membership in exactly one SQL SELECT, regardless of Distribution
    count — instrumented against the real production engine, not source
    text grep."""
    campaign, distribution_a, user = _distributed_piece_in_session(db_session)
    _campaign_b, distribution_b, _user_b = _distributed_piece_in_session(db_session)  # a different campaign, must not appear
    service = MeasurementService(db_session)
    e1, _ = service.create_distribution_evidence(
        distribution=distribution_a, campaign=campaign, period_start=_today() - timedelta(days=5), period_end=_today(),
        metric_values={"reach": Decimal("10")}, client_request_id=next_client_request_id(),
        source_reference=None, actor_user_id=user.id,
    )
    service.create_distribution_evidence_correction(
        distribution=distribution_a, campaign=campaign, target_evidence_public_id=e1.public_id,
        period_start=_today() - timedelta(days=4), period_end=_today(),
        metric_values={"reach": Decimal("20")}, client_request_id=next_client_request_id(),
        source_reference=None, correction_reason="probe", actor_user_id=user.id,
    )
    db_session.commit()

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        if "distribution_metric_evidence" in statement and statement.strip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        current = DistributionMetricEvidenceRepository(db_session).list_current_for_campaign(campaign.id)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert len(current) == 1, "[MEMBERSHIP] only the successor is current"
    assert len(statements) == 1, f"[SINGLE STATEMENT] list_current_for_campaign must issue exactly one SELECT, got {statements}"
    assert "NOT (EXISTS" in statements[0].upper(), "[SINGLE STATEMENT] the one SELECT must contain the anti-join"
    assert "JOIN" in statements[0].upper(), "[SINGLE STATEMENT] must reach Campaign via JOINs, not a second query"


def test_content_version_lookup_is_batched_not_per_observation(campaign_client_with_stages):
    """MVP-22A §64: prove ContentVersion public-id resolution for several
    distinct metrics/Distributions is one batched query, not one query
    per latest/earliest observation."""
    fixtures = campaign_client_with_stages
    for name in ("reach", "saves", "clicks"):
        content_id = _distributed(fixtures)
        _create_evidence(fixtures, content_id, values={name: 1})

    version_queries: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        if "content_versions" in statement and statement.strip().upper().startswith("SELECT"):
            version_queries.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        response = _rollup(fixtures)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert response.status_code == 200, response.text
    assert len(response.json()["metrics"]) == 3
    version_select_queries = [q for q in version_queries if "content_pieces" not in q]  # exclude joined queries elsewhere
    assert len(version_select_queries) <= 1, (
        f"[BATCHED LOOKUP] ContentVersion public-id resolution must be one batched query "
        f"across 3 distinct metrics/Distributions, got {len(version_select_queries)}: {version_select_queries}"
    )


def test_metric_value_loading_is_batched(campaign_client_with_stages):
    """MVP-22A §65: prove MetricValue loading does not scale with the
    number of current Evidence rows — one batched query regardless of
    how many Distributions/Evidence rows exist."""
    fixtures = campaign_client_with_stages
    for _ in range(4):
        content_id = _distributed(fixtures)
        _create_evidence(fixtures, content_id, values={"reach": 1})

    value_queries: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        if "metric_values" in statement and statement.strip().upper().startswith("SELECT"):
            value_queries.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        response = _rollup(fixtures)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert response.status_code == 200, response.text
    assert len(value_queries) == 1, f"[BATCHED LOOKUP] MetricValue loading must be one query regardless of Evidence count, got {len(value_queries)}"


def test_frozen_content_version_survives_a_planted_newer_version(db_session) -> None:
    """MVP-22A §63: direct-DB proof that Campaign rollup provenance uses
    ContentDistribution.content_version_id, never
    ContentVersionRepository.get_latest_for_piece — by planting a newer,
    unrelated ContentVersion row for the same Piece (bypassing the state
    machine, since DISTRIBUTED has zero outgoing edges) and confirming the
    rollup still reports the ORIGINAL frozen version."""
    campaign, distribution, user = _distributed_piece_in_session(db_session)
    original_version_id = distribution.content_version_id

    content_piece_id = db_session.execute(
        select(ContentDistribution.content_piece_id).where(ContentDistribution.id == distribution.id)
    ).scalar_one()
    planted = ContentVersion(
        public_id="CNV-PLANTEDXXXX2", content_piece_id=content_piece_id, payload={"planted": True}, created_by_user_id=user.id,
    )
    db_session.add(planted)
    db_session.commit()

    content_service = ContentService(db_session)
    latest_via_piece = content_service.versions.get_latest_for_piece(content_piece_id)
    assert latest_via_piece.id == planted.id, "[SETUP] the planted row must genuinely be 'latest' by the piece-scoped query"

    service = MeasurementService(db_session)
    service.create_distribution_evidence(
        distribution=distribution, campaign=campaign, period_start=_today() - timedelta(days=3), period_end=_today(),
        metric_values={"reach": Decimal("500")}, client_request_id=next_client_request_id(),
        source_reference=None, actor_user_id=user.id,
    )
    db_session.commit()

    current_rows = service.summarize_distribution_evidence_for_campaign(campaign_id=campaign.id)
    referenced_version_ids = {distribution_row.content_version_id for _e, distribution_row, _p, _entry, _values in current_rows}
    assert referenced_version_ids == {original_version_id}, (
        "[PROVENANCE VIOLATION] rollup must reference ContentDistribution.content_version_id directly, "
        "never re-derive 'latest' from the Piece"
    )
    assert planted.id not in referenced_version_ids


def test_same_created_at_deterministic_tie_break_across_distributions(db_session) -> None:
    """MVP-22A §69: force two independent current Evidence rows, on TWO
    DIFFERENT Distributions within the SAME Campaign, to share an
    identical created_at — latest/earliest must resolve deterministically
    via the internal id tie-break, never via incidental row/Distribution
    ordering, and the id itself must never leak into the public shape."""
    from decimal import Decimal as _Decimal

    from sqlalchemy import update

    campaign, distribution_a, distribution_b, user = _two_distributed_pieces_in_same_campaign(db_session)
    service = MeasurementService(db_session)
    e1, _ = service.create_distribution_evidence(
        distribution=distribution_a, campaign=campaign, period_start=_today() - timedelta(days=5), period_end=_today(),
        metric_values={"reach": _Decimal("10")}, client_request_id=next_client_request_id(),
        source_reference=None, actor_user_id=user.id,
    )
    db_session.commit()
    e2, _ = service.create_distribution_evidence(
        distribution=distribution_b, campaign=campaign, period_start=_today() - timedelta(days=5), period_end=_today(),
        metric_values={"reach": _Decimal("20")}, client_request_id=next_client_request_id(),
        source_reference=None, actor_user_id=user.id,
    )
    db_session.commit()
    assert e1.id != e2.id

    shared_ts = e1.created_at
    db_session.execute(
        update(DistributionMetricEvidence).where(DistributionMetricEvidence.id.in_([e1.id, e2.id])).values(created_at=shared_ts)
    )
    db_session.commit()
    db_session.refresh(e1)
    db_session.refresh(e2)
    assert e1.created_at == e2.created_at, "[SETUP] both rows must share an identical created_at"

    values_by_evidence_id = {e1.id: "10.0000", e2.id: "20.0000"}
    expected_latest_id = e1.id if e1.id > e2.id else e2.id
    expected_earliest_id = e2.id if expected_latest_id == e1.id else e1.id

    from app.measurement.schemas import build_campaign_distribution_evidence_rollup

    current_rows = service.summarize_distribution_evidence_for_campaign(campaign_id=campaign.id)
    content_version_ids = {distribution.content_version_id for _e, distribution, _p, _entry, _values in current_rows}
    content_version_public_ids = {
        v.id: v.public_id for v in ContentService(db_session).versions.list_for_ids(list(content_version_ids))
    }
    rollup = build_campaign_distribution_evidence_rollup(
        campaign_id=campaign.public_id, current_rows=current_rows, content_version_public_ids=content_version_public_ids,
    )
    reach = next(m for m in rollup.metrics if m.metric_name == "reach")
    assert reach.report_count == 2
    assert str(reach.latest.value) == values_by_evidence_id[expected_latest_id], "[TIE-BREAK] latest must resolve via max(created_at, id)"
    assert str(reach.earliest.value) == values_by_evidence_id[expected_earliest_id], "[TIE-BREAK] earliest must resolve via min(created_at, id)"
    assert not hasattr(reach.latest, "id") and not hasattr(reach.earliest, "id")


def test_campaign_rollup_read_vs_correction_on_one_of_two_distributions(postgres_engine):
    """MVP-22A §63/§68: real PostgreSQL two-connection race — a correction
    held open (flushed, uncommitted) on Distribution A while an
    independent connection reads the Campaign-wide rollup, which must see
    a coherent state for BOTH Distribution A (pre-commit: predecessor
    only) and the already-committed, untouched Distribution B
    simultaneously in the SAME single-statement read — never a hybrid
    partial-visibility result."""
    with Session(postgres_engine) as session:
        campaign, distribution_a, distribution_b, user = _two_distributed_pieces_in_same_campaign(session)
        campaign_id, distribution_a_id, distribution_b_id, user_id = campaign.id, distribution_a.id, distribution_b.id, user.id
        today = _today()
        service = MeasurementService(session)
        evidence_a, _created = service.create_distribution_evidence(
            distribution=distribution_a, campaign=campaign, period_start=today - timedelta(days=10), period_end=today,
            metric_values={"reach": Decimal("100")}, client_request_id=next_client_request_id(),
            source_reference=None, actor_user_id=user_id,
        )
        current_public_id = evidence_a.public_id
        evidence_b, _created = service.create_distribution_evidence(
            distribution=distribution_b, campaign=campaign, period_start=today - timedelta(days=10), period_end=today,
            metric_values={"reach": Decimal("777")}, client_request_id=next_client_request_id(),
            source_reference=None, actor_user_id=user_id,
        )
        stable_public_id = evidence_b.public_id

    ITERATIONS = 20
    for i in range(ITERATIONS):
        from app.campaigns.models import Campaign

        reader_conn = postgres_engine.connect()
        writer_conn = postgres_engine.connect()
        reader_pid = reader_conn.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
        writer_pid = writer_conn.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
        reader_conn.commit()
        writer_conn.commit()
        assert reader_pid != writer_pid, "[RACE] reader and writer must be genuinely distinct connections"

        ready_evt = threading.Event()
        proceed_evt = threading.Event()
        results: dict = {}
        errors: list[BaseException] = []

        def run_writer(target_public_id=current_public_id, reason=f"race-{i}"):
            try:
                with Session(bind=writer_conn) as writer_session:
                    campaign_local = writer_session.get(Campaign, campaign_id)
                    distribution_local = writer_session.get(ContentDistribution, distribution_a_id)
                    service_local = MeasurementService(writer_session)

                    def _pause_before_commit(_session):
                        ready_evt.set()
                        proceed_evt.wait(timeout=10)

                    event.listen(writer_session, "before_commit", _pause_before_commit)
                    try:
                        new_evidence, _created = service_local.create_distribution_evidence_correction(
                            distribution=distribution_local, campaign=campaign_local,
                            target_evidence_public_id=target_public_id,
                            period_start=today - timedelta(days=9), period_end=today,
                            metric_values={"reach": Decimal(str(200 + i))},
                            client_request_id=next_client_request_id(), source_reference=None,
                            correction_reason=reason, actor_user_id=user_id,
                        )
                        results["new_public_id"] = new_evidence.public_id
                    finally:
                        event.remove(writer_session, "before_commit", _pause_before_commit)
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        writer_thread = threading.Thread(target=run_writer)
        writer_thread.start()
        assert ready_evt.wait(timeout=10), "[RACE] writer never reached before_commit"

        with Session(bind=reader_conn) as reader_session:
            pre_rows = MeasurementService(reader_session).summarize_distribution_evidence_for_campaign(campaign_id=campaign_id)
        pre_current_ids = {row[0].public_id for row in pre_rows}
        assert pre_current_ids == {current_public_id, stable_public_id}, (
            f"[RACE] pre-commit rollup must show predecessor-on-A + untouched-B as current, got {pre_current_ids}"
        )

        proceed_evt.set()
        writer_thread.join(timeout=20)
        assert not errors, errors
        assert "new_public_id" in results

        with Session(postgres_engine) as post_session:
            post_rows = MeasurementService(post_session).summarize_distribution_evidence_for_campaign(campaign_id=campaign_id)
        post_current_ids = {row[0].public_id for row in post_rows}
        assert post_current_ids == {results["new_public_id"], stable_public_id}, (
            f"[RACE] post-commit rollup must show successor-on-A + untouched-B as current, got {post_current_ids}"
        )

        current_public_id = results["new_public_id"]
        reader_conn.close()
        writer_conn.close()
