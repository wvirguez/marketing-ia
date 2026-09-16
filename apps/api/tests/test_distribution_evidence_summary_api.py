"""Distribution Evidence Summary HTTP contract (MVP-21, frozen by
MVP-21A/MVP-21A-R1); real PostgreSQL only.

Reuses the Evidence fixtures/helpers already established in
``tests/test_distribution_evidence_api.py`` (which itself reuses
``tests/test_distribution_api.py`` and ``tests/test_content_api.py``) to
reach a genuinely DISTRIBUTED ContentPiece + ContentDistribution before
exercising the summary route.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.content.models import ContentDistribution
from app.measurement.models import DistributionMetricEvidence, MetricEntry, MetricValue
from app.measurement.repository import DistributionMetricEvidenceRepository
from app.measurement.schemas import build_distribution_evidence_summary
from app.measurement.service import MeasurementService
from app.persistence.session import get_engine
from app.workspaces.models import MembershipRole
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.measurementtest import next_client_request_id
from tests.settingstest import add_member_to_workspace, login_as
from tests.test_content_api import _lifecycle_path, _record_content_piece
from tests.test_distribution_api import campaign_client_with_stages  # noqa: F401 (fixture)
from tests.test_distribution_evidence_api import (
    _correct_evidence,
    _create_evidence,
    _distributed,
    _distributed_piece_in_session,
    _evidence_path,
    _today,
    _total_count,
)

pytestmark = pytest.mark.postgres


def _summary_path(fixtures, content_id: str) -> str:
    return _evidence_path(fixtures, content_id, "/summary")


def _summary(fixtures, content_id: str):
    return fixtures["client"].get(_summary_path(fixtures, content_id))


def _metric(body: dict, name: str) -> dict:
    return next(m for m in body["metrics"] if m["metric_name"] == name)


# --- no Distribution / zero evidence --------------------------------------------


def test_no_distribution_returns_exact_null_provenance_shape(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)

    response = _summary(fixtures, content_id)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "content_piece_id": content_id,
        "distribution_id": None,
        "content_version_id": None,
        "channel": None,
        "metrics": [],
    }


def test_distribution_with_zero_current_evidence_populates_provenance(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)

    response = _summary(fixtures, content_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["content_piece_id"] == content_id
    assert body["distribution_id"] is not None and body["distribution_id"].startswith("DST-")
    assert body["content_version_id"] is not None and body["content_version_id"].startswith("CNV-")
    assert body["channel"] is not None
    assert body["metrics"] == []


# --- source-of-truth / provenance ------------------------------------------------


def test_summary_provenance_matches_distribution_row(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    _create_evidence(fixtures, content_id)

    body = _summary(fixtures, content_id).json()
    detail = fixtures["client"].get(_lifecycle_path(fixtures, content_id, "")).json()
    with Session(get_engine()) as session:
        distribution = session.execute(
            select(ContentDistribution).where(ContentDistribution.public_id == body["distribution_id"])
        ).scalar_one()
    assert body["channel"] == detail["distribution"]["channel"] == distribution.channel
    assert body["content_version_id"] == detail["latest_version"]["id"], (
        "[PROVENANCE] the Distribution's own frozen version, which at this point in the "
        "flow is still the Piece's latest version too"
    )


# --- grouping / case sensitivity -------------------------------------------------


def test_case_sensitive_metric_names_produce_separate_summary_rows(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    _create_evidence(fixtures, content_id, values={"Clicks": 10, "clicks": 20})

    body = _summary(fixtures, content_id).json()
    names = {m["metric_name"] for m in body["metrics"]}
    assert names == {"Clicks", "clicks"}, "[NO CASE-FOLDING] distinct stored identities must remain distinct summary rows"
    assert _metric(body, "Clicks")["latest_value"] == "10.0000"
    assert _metric(body, "clicks")["latest_value"] == "20.0000"


def test_metric_semantic_uniformity_no_type_inference(campaign_client_with_stages):
    """MVP-21A §G/§4: names that look semantically different (a rate, a
    currency amount, a cumulative counter) must be summarized identically
    — pure count/latest/earliest, no name-based branching."""
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    _create_evidence(
        fixtures, content_id,
        values={"clicks": 10, "conversion_rate": "0.05", "revenue": 199.99, "cumulative_views": 100000},
    )

    body = _summary(fixtures, content_id).json()
    names = {m["metric_name"] for m in body["metrics"]}
    assert names == {"clicks", "conversion_rate", "revenue", "cumulative_views"}
    for metric in body["metrics"]:
        assert set(metric.keys()) == {
            "metric_name", "report_count", "latest_value", "latest_period_start", "latest_period_end",
            "latest_reported_at", "earliest_value", "earliest_reported_at",
        }, "[NO TYPE INFERENCE] every metric must expose exactly the same, uniform field set"


# --- correction chains ------------------------------------------------------------


def test_correction_chain_of_two_only_terminal_contributes(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    created = _create_evidence(fixtures, content_id, values={"reach": 100}).json()
    _correct_evidence(fixtures, content_id, created["id"], values={"reach": 200})

    body = _summary(fixtures, content_id).json()
    reach = _metric(body, "reach")
    assert reach["report_count"] == 1, "[CORRECTION] superseded predecessor must not contribute"
    assert reach["latest_value"] == "200.0000"
    assert reach["earliest_value"] == "200.0000"


def test_correction_chain_of_three_only_terminal_contributes(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    e1 = _create_evidence(fixtures, content_id, values={"reach": 100}).json()
    e2 = _correct_evidence(fixtures, content_id, e1["id"], values={"reach": 200}).json()
    _correct_evidence(fixtures, content_id, e2["id"], values={"reach": 300})

    body = _summary(fixtures, content_id).json()
    reach = _metric(body, "reach")
    assert reach["report_count"] == 1
    assert reach["latest_value"] == "300.0000"
    assert reach["earliest_value"] == "300.0000"


# --- independent reports -----------------------------------------------------------


def test_independent_reports_both_current_report_count_correct(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    today = _today()
    # NOTE: every period_end below is pinned to `today` — the fixture's
    # Distribution was recorded "now", and Evidence rejects any period_end
    # before Distribution.distributed_at's date (or after today); only
    # period_start is free to vary, giving genuinely different period
    # lengths while keeping every row otherwise valid.
    _create_evidence(
        fixtures, content_id, values={"reach": 100},
        period_start=(today - timedelta(days=20)).isoformat(), period_end=today.isoformat(),
        client_request_id=next_client_request_id(),
    )
    _create_evidence(
        fixtures, content_id, values={"reach": 200},
        period_start=(today - timedelta(days=10)).isoformat(), period_end=today.isoformat(),
        client_request_id=next_client_request_id(),
    )

    body = _summary(fixtures, content_id).json()
    reach = _metric(body, "reach")
    assert reach["report_count"] == 2, "[INDEPENDENT REPORTS] neither supersedes the other — both must count"
    assert reach["latest_value"] == "200.0000", "[REPORTING CHRONOLOGY] latest = most recently reported, not largest period_end"
    assert reach["earliest_value"] == "100.0000"
    for key in ("sum", "average", "min", "max", "trend", "percent_change"):
        assert key not in reach, f"[NO ARITHMETIC] forbidden aggregation field '{key}' must never appear"


def test_independent_reports_with_overlapping_periods(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    today = _today()
    _create_evidence(
        fixtures, content_id, values={"reach": 111},
        period_start=(today - timedelta(days=20)).isoformat(), period_end=today.isoformat(),
        client_request_id=next_client_request_id(),
    )
    _create_evidence(
        fixtures, content_id, values={"reach": 222},
        period_start=(today - timedelta(days=15)).isoformat(), period_end=today.isoformat(),
        client_request_id=next_client_request_id(),
    )

    body = _summary(fixtures, content_id).json()
    reach = _metric(body, "reach")
    assert reach["report_count"] == 2, "[OVERLAPPING PERIODS] overlap does not collapse independent reports into one"


def test_independent_reports_with_identical_periods(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    today = _today()
    shared_start, shared_end = (today - timedelta(days=5)).isoformat(), today.isoformat()
    _create_evidence(
        fixtures, content_id, values={"reach": 10},
        period_start=shared_start, period_end=shared_end, client_request_id=next_client_request_id(),
    )
    _create_evidence(
        fixtures, content_id, values={"reach": 20},
        period_start=shared_start, period_end=shared_end, client_request_id=next_client_request_id(),
    )

    body = _summary(fixtures, content_id).json()
    reach = _metric(body, "reach")
    assert reach["report_count"] == 2


# --- reporting chronology vs period ------------------------------------------------


def test_period_does_not_determine_latest_earliest(campaign_client_with_stages):
    """A report with an earlier measurement period, submitted later in real
    time, must still be `latest` — reporting chronology (created_at), never
    period chronology."""
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    today = _today()
    # First call: a SHORT, recent-looking period (period_start close to
    # today) but posted FIRST in real time. Second call: a LONG,
    # older-looking period (period_start far from today) but posted
    # SECOND — deliberately the opposite of period_start ordering, so a
    # (wrong) implementation that picked "latest" by max(period_start)
    # would return the first call's value instead of the truly
    # most-recently-reported one.
    _create_evidence(
        fixtures, content_id, values={"reach": 900},
        period_start=(today - timedelta(days=5)).isoformat(), period_end=today.isoformat(),
        client_request_id=next_client_request_id(),
    )
    _create_evidence(
        fixtures, content_id, values={"reach": 100},
        period_start=(today - timedelta(days=60)).isoformat(), period_end=today.isoformat(),
        client_request_id=next_client_request_id(),
    )

    body = _summary(fixtures, content_id).json()
    reach = _metric(body, "reach")
    assert reach["latest_value"] == "100.0000", "[REPORTING CHRONOLOGY] the second POST is latest regardless of its older-looking period"


# --- no write side effects / analysis isolation ------------------------------------


def test_summary_reads_have_zero_write_side_effects(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    _create_evidence(fixtures, content_id)

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
        assert _summary(fixtures, content_id).status_code == 200
    after = _counts()
    assert before == after, "[READ-ONLY] repeated summary GETs must never write AuditEvent/MetricEntry/MetricValue/Evidence rows"


def test_summary_read_does_not_alter_aggregate_metrics_or_analysis(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    campaign_id = fixtures["campaign_id"]
    csrf_headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    _create_evidence(fixtures, content_id)

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
        assert _summary(fixtures, content_id).status_code == 200

    metrics_after = fixtures["client"].get(f"/api/v1/campaigns/{campaign_id}/metrics").json()
    assert metrics_before == metrics_after, "[ANALYSIS ISOLATION] summary reads must not alter the aggregate /metrics listing"

    run_response = fixtures["client"].post(
        f"/api/v1/campaigns/{campaign_id}/analysis/run", json={"client_request_id": next_client_request_id()}, headers=csrf_headers
    )
    assert run_response.status_code == 200, run_response.text
    analysis_before = fixtures["client"].get(f"/api/v1/campaigns/{campaign_id}/analysis").json()

    for _ in range(3):
        assert _summary(fixtures, content_id).status_code == 200

    analysis_after = fixtures["client"].get(f"/api/v1/campaigns/{campaign_id}/analysis").json()
    assert analysis_before == analysis_after, "[ANALYSIS ISOLATION] summary reads must not alter analysis results"


# --- authorization -------------------------------------------------------------


def test_summary_open_to_any_active_member_not_just_owner(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    _create_evidence(fixtures, content_id)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}
    response = _summary(member_fixtures, content_id)
    assert response.status_code == 200, response.text


def test_summary_get_does_not_require_csrf(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    response = _summary(fixtures, content_id)
    assert response.status_code == 200


# --- tenancy ---------------------------------------------------------------------


def test_summary_other_workspace_cannot_read(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    _create_evidence(fixtures, content_id)

    outsider = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    register_and_get_csrf(outsider, display_name="Other workspace summary reader")
    response = outsider.get(_summary_path(fixtures, content_id))
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_summary_same_workspace_different_campaign_is_rejected(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    _create_evidence(fixtures, content_id)

    csrf_b = fixtures["client"].get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = fixtures["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name="Campaign B (same workspace)"), headers={"X-CSRF-Token": csrf_b}
    ).json()
    other_campaign_id = body_b["campaign"]["id"]

    response = fixtures["client"].get(
        f"/api/v1/campaigns/{other_campaign_id}/content/{content_id}/distribution/evidence/summary"
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_summary_same_campaign_different_piece_no_leakage(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id_a = _distributed(fixtures)
    _create_evidence(fixtures, content_id_a, values={"reach": 999})

    content_id_b = _distributed(fixtures)
    body_b = _summary(fixtures, content_id_b).json()
    assert body_b["metrics"] == [], "[TENANCY] a sibling Piece's own summary must never include another Piece's Evidence"
    assert body_b["content_piece_id"] == content_id_b


def test_summary_unknown_content_piece_is_non_leaky(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    _distributed(fixtures)
    response = fixtures["client"].get(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/content/CNT-UNKNOWNUNKNOWN/distribution/evidence/summary"
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


# --- domain-level: determinism / single-statement proof (direct DB) --------------


def test_same_created_at_deterministic_tie_break(db_session) -> None:
    """MVP-21A-R1 §L/§M/§N: force two independent current Evidence rows for
    the same metric to share an identical ``created_at``, differing only by
    internal id — latest/earliest must resolve deterministically via that
    id, never via incidental ordering, and the id itself must never leak
    into the public summary shape."""
    campaign, distribution, user = _distributed_piece_in_session(db_session)
    service = MeasurementService(db_session)
    e1, _ = service.create_distribution_evidence(
        distribution=distribution, campaign=campaign, period_start=_today() - timedelta(days=5), period_end=_today(),
        metric_values={"reach": Decimal("10")}, client_request_id=next_client_request_id(),
        source_reference=None, actor_user_id=user.id,
    )
    db_session.commit()
    e2, _ = service.create_distribution_evidence(
        distribution=distribution, campaign=campaign, period_start=_today() - timedelta(days=5), period_end=_today(),
        metric_values={"reach": Decimal("20")}, client_request_id=next_client_request_id(),
        source_reference=None, actor_user_id=user.id,
    )
    db_session.commit()
    assert e1.id != e2.id

    shared_ts = e1.created_at
    db_session.execute(
        update(DistributionMetricEvidence)
        .where(DistributionMetricEvidence.id.in_([e1.id, e2.id]))
        .values(created_at=shared_ts)
    )
    db_session.commit()
    db_session.refresh(e1)
    db_session.refresh(e2)
    assert e1.created_at == e2.created_at, "[SETUP] both rows must share an identical created_at"

    values_by_evidence_id = {e1.id: Decimal("10.0000"), e2.id: Decimal("20.0000")}
    expected_latest_id = e1.id if e1.id > e2.id else e2.id
    expected_earliest_id = e2.id if expected_latest_id == e1.id else e1.id

    current_rows = service.summarize_distribution_evidence(distribution_id=distribution.id)
    summary = build_distribution_evidence_summary(
        content_piece_id="ignored", distribution_id=None, content_version_id=None, channel=None,
        current_rows=current_rows,
    )
    reach = next(m for m in summary.metrics if m.metric_name == "reach")
    assert reach.report_count == 2
    assert reach.latest_value == values_by_evidence_id[expected_latest_id], "[TIE-BREAK] latest must resolve via max(created_at, id)"
    assert reach.earliest_value == values_by_evidence_id[expected_earliest_id], "[TIE-BREAK] earliest must resolve via min(created_at, id)"
    assert not hasattr(reach, "id"), "[NO INTERNAL ID] the internal tie-break id must never appear on MetricSummaryItem"
    assert set(reach.model_dump().keys()) == {
        "metric_name", "report_count", "latest_value", "latest_period_start", "latest_period_end",
        "latest_reported_at", "earliest_value", "earliest_reported_at",
    }


def test_current_evidence_membership_is_a_single_select_statement(db_session) -> None:
    """MVP-21A-R1 §F/MVP-21B §45: prove ``list_current_for_distribution``
    resolves current-Evidence membership in exactly one SQL SELECT — no
    separate superseded-id fetch, no second query re-deriving membership.
    The later, independent batched MetricValue lookup does not count as a
    second *membership* statement (MVP-21B §45)."""
    campaign, distribution, user = _distributed_piece_in_session(db_session)
    service = MeasurementService(db_session)
    e1, _ = service.create_distribution_evidence(
        distribution=distribution, campaign=campaign, period_start=_today() - timedelta(days=5), period_end=_today(),
        metric_values={"reach": Decimal("10")}, client_request_id=next_client_request_id(),
        source_reference=None, actor_user_id=user.id,
    )
    service.create_distribution_evidence_correction(
        distribution=distribution, campaign=campaign, target_evidence_public_id=e1.public_id,
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
        current = DistributionMetricEvidenceRepository(db_session).list_current_for_distribution(distribution.id)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert len(current) == 1, "[MEMBERSHIP] only the successor is current"
    membership_statements = [s for s in statements if "NOT (EXISTS" in s.upper() or "NOT EXISTS" in s.upper()]
    assert len(membership_statements) == 1, (
        f"[SINGLE STATEMENT] expected exactly one anti-join SELECT, got {len(membership_statements)}: {statements}"
    )
    assert len(statements) == 1, f"[SINGLE STATEMENT] list_current_for_distribution must issue exactly one SELECT, got {statements}"
