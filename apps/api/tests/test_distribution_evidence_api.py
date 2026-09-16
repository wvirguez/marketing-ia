"""Distribution-linked Measurement Evidence HTTP contract, tenancy,
idempotency, and isolation tests (MVP-19B); real PostgreSQL only.

Reuses the Distribution fixtures already established in
``tests/test_distribution_api.py`` (which themselves reuse
``tests/test_content_api.py``'s lifecycle helpers) to reach a genuinely
DISTRIBUTED ContentPiece + ContentDistribution before exercising Evidence.
"""

from __future__ import annotations

import re
from datetime import date, timedelta, timezone
from datetime import datetime as dt
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import ActorType, AuditEvent
from app.audit.repository import AuditEventRepository
from app.content.models import ContentApprovalStatus, ContentDistribution
from app.content.service import ContentService
from app.core.ids import generate_public_id
from app.measurement.models import DistributionMetricEvidence, MetricEntry, MetricValue
from app.measurement.service import MeasurementService
from app.persistence.session import get_engine
from app.users.models import User
from app.workspaces.models import Membership, MembershipRole, MembershipStatus
from tests.campaignstest import register_and_get_csrf
from tests.contenttest import build_plan_with_item, default_piece_fields, default_version_payload, make_user
from tests.measurementtest import next_client_request_id
from tests.settingstest import add_member_to_workspace, login_as
from tests.test_content_api import _lifecycle_path, _post
from tests.test_distribution_api import _approved, _path, campaign_client_with_stages  # noqa: F401 (fixture)

pytestmark = pytest.mark.postgres

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _distributed(fixtures) -> str:
    content_id = _approved(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    ready = fixtures["client"].post(_path(fixtures, content_id, "mark-ready-for-distribution"), headers=headers)
    assert ready.status_code == 201, ready.text
    recorded = fixtures["client"].post(
        _path(fixtures, content_id, "record-distributed"), json={"external_reference": "creator-dashboard:xyz"}, headers=headers
    )
    assert recorded.status_code == 200, recorded.text
    return content_id


def _evidence_path(fixtures, content_id: str, suffix: str = "") -> str:
    return _lifecycle_path(fixtures, content_id, f"/distribution/evidence{suffix}")


def _today() -> date:
    """Evidence periods must fall on/after the Distribution's own
    ``distributed_at`` UTC date and on/before today — since the test
    fixtures record distribution "now," only a period anchored to the
    real current date is always valid, regardless of when this suite
    runs."""
    return dt.now(timezone.utc).date()


def _create_evidence(fixtures, content_id: str, **overrides):
    today = _today()
    body = {
        "period_start": (today - timedelta(days=5)).isoformat(),
        "period_end": today.isoformat(),
        "values": {"reach": 500, "saves": 12},
        "client_request_id": next_client_request_id(),
        "source_reference": "creator dashboard screenshot",
    }
    body.update(overrides)
    return _post(fixtures, _evidence_path(fixtures, content_id), body)


def _correct_evidence(fixtures, content_id: str, evidence_id: str, **overrides):
    today = _today()
    body = {
        "period_start": (today - timedelta(days=5)).isoformat(),
        "period_end": today.isoformat(),
        "values": {"reach": 600, "saves": 20},
        "client_request_id": next_client_request_id(),
        "correction_reason": "Corrected platform export.",
    }
    body.update(overrides)
    return _post(fixtures, _evidence_path(fixtures, content_id, f"/{evidence_id}/corrections"), body)


# --- happy path -------------------------------------------------------------


def test_create_evidence_happy_path(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)

    response = _create_evidence(fixtures, content_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"].startswith("DME-")
    assert body["metric_entry_id"].startswith("MET-")
    assert body["distribution_id"].startswith("DST-")
    assert body["content_piece_id"] == content_id
    assert body["evidence_scope"] == "DISTRIBUTION_SPECIFIC"
    assert body["values"] == {"reach": "500.0000", "saves": "12.0000"}
    assert body["channel"] == fixtures["client"].get(_lifecycle_path(fixtures, content_id, "")).json()["piece"]["channel"]
    assert body["source"] == "MANUAL"
    assert body["is_current"] is True
    assert body["supersedes_evidence_id"] is None
    assert body["correction_reason"] is None
    assert not _UUID_RE.search(response.text), "[NO RAW UUID] response must expose only public ids"
    assert "causal" not in response.text.lower()
    assert "attribut" not in response.text.lower()

    listed = fixtures["client"].get(_evidence_path(fixtures, content_id))
    assert listed.status_code == 200, listed.text
    listed_body = listed.json()
    assert listed_body["total"] == 1
    assert listed_body["items"][0]["id"] == body["id"]
    assert listed_body["items"][0]["is_current"] is True


def test_evidence_requires_distributed_piece_and_distribution(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _approved(fixtures)  # APPROVED, not yet READY/DISTRIBUTED

    # No Distribution exists yet at all — GET is a truthful empty list, not an error.
    listed = fixtures["client"].get(_evidence_path(fixtures, content_id))
    assert listed.status_code == 200 and listed.json() == {"items": [], "limit": 20, "offset": 0, "total": 0}

    create_response = _create_evidence(fixtures, content_id)
    assert create_response.status_code == 409, create_response.text
    assert create_response.json()["error"]["code"] == "INVALID_LIFECYCLE_TRANSITION"

    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    ready = fixtures["client"].post(_path(fixtures, content_id, "mark-ready-for-distribution"), headers=headers)
    assert ready.status_code == 201, ready.text

    # READY_FOR_DISTRIBUTION (Distribution exists, but status == READY, not DISTRIBUTED)
    still_blocked = _create_evidence(fixtures, content_id)
    assert still_blocked.status_code == 409
    assert still_blocked.json()["error"]["code"] == "INVALID_LIFECYCLE_TRANSITION"

    fixtures["client"].post(_path(fixtures, content_id, "record-distributed"), json={}, headers=headers)
    allowed = _create_evidence(fixtures, content_id)
    assert allowed.status_code == 201, allowed.text


# --- validation --------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides,expected_status",
    [
        ({"values": {}}, 422),
        ({"values": {"  impressions  ": 10, "impressions": 5}}, 422),
        ({"values": {"x" * 101: 1}}, 422),
        ({"values": {"reach": "NaN"}}, 422),
        ({"values": {"reach": "1" * 17}}, 422),
        ({"values": {"reach": "1.23456"}}, 422),
        ({"period_start": "2026-01-10", "period_end": "2026-01-05"}, 422),
        ({"period_end": "2099-01-01"}, 422),
        ({"source_reference": "x" * 2049}, 422),
        ({"unexpected_field": "x"}, 422),
    ],
)
def test_create_evidence_validation(campaign_client_with_stages, overrides, expected_status):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    response = _create_evidence(fixtures, content_id, **overrides)
    assert response.status_code == expected_status, response.text


def test_create_evidence_rejects_period_before_distribution(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    response = _create_evidence(fixtures, content_id, period_start="2020-01-01", period_end="2020-01-02")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "EVIDENCE_PERIOD_INVALID"


def test_correction_requires_reason(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    evidence_id = _create_evidence(fixtures, content_id).json()["id"]
    for overrides, expected in (
        ({"correction_reason": ""}, 422),
        ({"correction_reason": "x" * 501}, 422),
    ):
        response = _correct_evidence(fixtures, content_id, evidence_id, **overrides)
        assert response.status_code == expected, response.text


# --- idempotency: create ----------------------------------------------------


def test_create_evidence_idempotent_replay_returns_200(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    key = next_client_request_id()
    first = _create_evidence(fixtures, content_id, client_request_id=key)
    assert first.status_code == 201, first.text
    second = _create_evidence(fixtures, content_id, client_request_id=key)
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["metric_entry_id"] == first.json()["metric_entry_id"]

    listed = fixtures["client"].get(_evidence_path(fixtures, content_id))
    assert listed.json()["total"] == 1, "[IDEMPOTENCY] a replay must never create a second row"


def test_create_evidence_conflicting_replay_returns_409(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    key = next_client_request_id()
    first = _create_evidence(fixtures, content_id, client_request_id=key)
    assert first.status_code == 201, first.text
    conflicting = _create_evidence(fixtures, content_id, client_request_id=key, values={"reach": 999})
    assert conflicting.status_code == 409, conflicting.text
    assert conflicting.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_create_evidence_key_collides_with_plain_metric_entry_key(campaign_client_with_stages):
    """MVP-19B §25: the idempotency key space is deliberately shared with
    the plain, pre-existing MetricEntry.client_request_id uniqueness."""
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    key = next_client_request_id()
    campaign_id = fixtures["campaign_id"]
    plain = fixtures["client"].post(
        f"/api/v1/campaigns/{campaign_id}/metrics",
        json={
            "period_start": "2026-01-01", "period_end": "2026-01-31", "channel": "Instagram",
            "source": "MANUAL", "client_request_id": key, "values": {"clicks": 1},
        },
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert plain.status_code == 201, plain.text
    conflicting = _create_evidence(fixtures, content_id, client_request_id=key)
    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


# --- idempotency: correction (mandatory precedence) --------------------------


def test_correction_idempotent_replay_precedence_over_stale_target(campaign_client_with_stages):
    """MVP-19B §27/§69 (mandatory): an exact successful replay of a prior
    correction must return 200 with that correction's Evidence — even
    though, by the time of the replay, its OWN target is no longer the
    current leaf (a second, different correction has since superseded it
    further)."""
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    e1 = _create_evidence(fixtures, content_id).json()["id"]

    key = next_client_request_id()
    first_correction = _correct_evidence(fixtures, content_id, e1, client_request_id=key)
    assert first_correction.status_code == 201, first_correction.text
    e2 = first_correction.json()["id"]

    second_correction = _correct_evidence(fixtures, content_id, e2)
    assert second_correction.status_code == 201, second_correction.text
    e3 = second_correction.json()["id"]
    assert e3 != e2

    # e1 is now stale (superseded by e2, which is itself superseded by e3).
    # Replaying the FIRST correction's exact request key must still return
    # e2 with 200 — never a 409 stale-target conflict.
    replay = _correct_evidence(fixtures, content_id, e1, client_request_id=key)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == e2


def test_conflicting_correction_replay_returns_409(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    e1 = _create_evidence(fixtures, content_id).json()["id"]
    key = next_client_request_id()
    first = _correct_evidence(fixtures, content_id, e1, client_request_id=key)
    assert first.status_code == 201, first.text
    conflicting = _correct_evidence(fixtures, content_id, e1, client_request_id=key, correction_reason="A different reason.")
    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_correction_against_already_superseded_target_is_rejected(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    e1 = _create_evidence(fixtures, content_id).json()["id"]
    first_correction = _correct_evidence(fixtures, content_id, e1)
    assert first_correction.status_code == 201, first_correction.text

    stale_attempt = _correct_evidence(fixtures, content_id, e1)
    assert stale_attempt.status_code == 409, stale_attempt.text
    assert stale_attempt.json()["error"]["code"] == "EVIDENCE_CORRECTION_TARGET_STALE"


def test_correction_history_and_lineage(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    e1 = _create_evidence(fixtures, content_id).json()["id"]
    e2 = _correct_evidence(fixtures, content_id, e1).json()["id"]

    listed = fixtures["client"].get(_evidence_path(fixtures, content_id))
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert listed.json()["total"] == 2
    by_id = {item["id"]: item for item in items}
    assert by_id[e1]["is_current"] is False
    assert by_id[e1]["supersedes_evidence_id"] is None
    assert by_id[e2]["is_current"] is True
    assert by_id[e2]["supersedes_evidence_id"] == e1
    assert by_id[e2]["correction_reason"] == "Corrected platform export."
    # newest-first
    assert items[0]["id"] == e2


def test_evidence_list_pagination(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    evidence_id = _create_evidence(fixtures, content_id).json()["id"]
    for _ in range(3):
        evidence_id = _correct_evidence(fixtures, content_id, evidence_id).json()["id"]

    page = fixtures["client"].get(_evidence_path(fixtures, content_id) + "?limit=2&offset=0")
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["total"] == 4
    assert len(body["items"]) == 2
    assert body["limit"] == 2 and body["offset"] == 0

    bad = fixtures["client"].get(_evidence_path(fixtures, content_id) + "?limit=0")
    assert bad.status_code == 422
    bad2 = fixtures["client"].get(_evidence_path(fixtures, content_id) + "?limit=101")
    assert bad2.status_code == 422
    bad3 = fixtures["client"].get(_evidence_path(fixtures, content_id) + "?offset=-1")
    assert bad3.status_code == 422


# --- CSRF --------------------------------------------------------------------


def test_evidence_mutation_requires_csrf(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    no_csrf = fixtures["client"].post(_evidence_path(fixtures, content_id), json={})
    assert no_csrf.status_code == 403 and no_csrf.json()["error"]["code"] == "CSRF_INVALID"

    evidence_id = _create_evidence(fixtures, content_id).json()["id"]
    no_csrf_correction = fixtures["client"].post(_evidence_path(fixtures, content_id, f"/{evidence_id}/corrections"), json={})
    assert no_csrf_correction.status_code == 403 and no_csrf_correction.json()["error"]["code"] == "CSRF_INVALID"


def test_evidence_get_does_not_require_csrf(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    response = fixtures["client"].get(_evidence_path(fixtures, content_id))
    assert response.status_code == 200


# --- authorization / authentication ------------------------------------------


def test_evidence_open_to_any_active_member_not_just_owner(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}
    response = _create_evidence(member_fixtures, content_id)
    assert response.status_code == 201, response.text


def test_evidence_requires_authentication_and_active_membership(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    anonymous_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    response = anonymous_client.get(_evidence_path(fixtures, content_id))
    assert response.status_code == 401 and response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

    me = fixtures["client"].get("/api/v1/users/me").json()
    with Session(get_engine()) as session:
        user = session.execute(select(User).where(User.public_id == me["id"])).scalar_one()
        membership = session.execute(select(Membership).where(Membership.user_id == user.id)).scalar_one()
        membership.status = MembershipStatus.REVOKED
        session.commit()
    revoked_response = _create_evidence(fixtures, content_id)
    assert revoked_response.status_code == 403 and revoked_response.json()["error"]["code"] == "FORBIDDEN"


# --- tenancy -------------------------------------------------------------------


def test_other_workspace_cannot_read_or_mutate_evidence(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    evidence_id = _create_evidence(fixtures, content_id).json()["id"]

    outsider = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = register_and_get_csrf(outsider, display_name="Other workspace evidence")
    headers = {"X-CSRF-Token": token}
    today = _today()
    valid_create_body = {
        "period_start": (today - timedelta(days=5)).isoformat(), "period_end": today.isoformat(),
        "values": {"reach": 1}, "client_request_id": next_client_request_id(),
    }
    valid_correction_body = {**valid_create_body, "client_request_id": next_client_request_id(), "correction_reason": "x"}
    get_response = outsider.get(_evidence_path(fixtures, content_id))
    assert get_response.status_code == 403 and get_response.json()["error"]["code"] == "FORBIDDEN"
    post_response = outsider.post(_evidence_path(fixtures, content_id), json=valid_create_body, headers=headers)
    assert post_response.status_code == 403 and post_response.json()["error"]["code"] == "FORBIDDEN"
    correction_response = outsider.post(
        _evidence_path(fixtures, content_id, f"/{evidence_id}/corrections"), json=valid_correction_body, headers=headers
    )
    assert correction_response.status_code == 403 and correction_response.json()["error"]["code"] == "FORBIDDEN"


def test_unknown_or_foreign_content_piece_is_non_leaky(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    _distributed(fixtures)
    response = fixtures["client"].get(_evidence_path(fixtures, "CNT-UNKNOWNUNKNOWN"))
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_correction_target_from_a_different_content_piece_is_rejected(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id_a = _distributed(fixtures)
    evidence_a = _create_evidence(fixtures, content_id_a).json()["id"]

    content_id_b = _distributed(fixtures)
    cross_correction = _correct_evidence(fixtures, content_id_b, evidence_a)
    assert cross_correction.status_code == 403, cross_correction.text
    assert cross_correction.json()["error"]["code"] == "FORBIDDEN"


# --- aggregate / analysis isolation (mandatory) -------------------------------


def test_evidence_metric_entry_excluded_from_aggregate_metrics_listing(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    campaign_id = fixtures["campaign_id"]
    csrf_headers = {"X-CSRF-Token": fixtures["csrf_token"]}

    aggregate = fixtures["client"].post(
        f"/api/v1/campaigns/{campaign_id}/metrics",
        json={
            "period_start": "2025-12-01", "period_end": "2025-12-31", "channel": "Instagram",
            "source": "MANUAL", "client_request_id": next_client_request_id(), "values": {"clicks": 100},
        },
        headers=csrf_headers,
    )
    assert aggregate.status_code == 201, aggregate.text
    aggregate_metric_id = aggregate.json()["id"]

    evidence_response = _create_evidence(fixtures, content_id)
    assert evidence_response.status_code == 201, evidence_response.text
    evidence_metric_id = evidence_response.json()["metric_entry_id"]
    assert evidence_metric_id != aggregate_metric_id

    listing = fixtures["client"].get(f"/api/v1/campaigns/{campaign_id}/metrics")
    listed_ids = {item["id"] for item in listing.json()["items"]}
    assert aggregate_metric_id in listed_ids, "[AGGREGATE ISOLATION] the aggregate entry must remain visible"
    assert evidence_metric_id not in listed_ids, "[AGGREGATE ISOLATION] the Evidence-linked entry must be excluded"


def test_evidence_metric_entry_excluded_from_analysis_pipeline(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    campaign_id = fixtures["campaign_id"]
    csrf_headers = {"X-CSRF-Token": fixtures["csrf_token"]}

    for period_start, period_end, clicks in (("2025-12-01", "2025-12-31", 100), ("2026-01-01", "2026-01-31", 150)):
        response = fixtures["client"].post(
            f"/api/v1/campaigns/{campaign_id}/metrics",
            json={
                "period_start": period_start, "period_end": period_end, "channel": "Instagram",
                "source": "MANUAL", "client_request_id": next_client_request_id(), "values": {"clicks": clicks},
            },
            headers=csrf_headers,
        )
        assert response.status_code == 201, response.text

    evidence_response = _create_evidence(fixtures, content_id)
    assert evidence_response.status_code == 201, evidence_response.text

    run_response = fixtures["client"].post(
        f"/api/v1/campaigns/{campaign_id}/analysis/run", json={"client_request_id": next_client_request_id()}, headers=csrf_headers
    )
    assert run_response.status_code == 200, run_response.text
    assert run_response.json()["status"] == "COMPLETED"

    analysis = fixtures["client"].get(f"/api/v1/campaigns/{campaign_id}/analysis")
    observation_entry_ids = set()
    with Session(get_engine()) as session:
        for entry in session.execute(select(MetricEntry).where(MetricEntry.campaign_id.isnot(None))).scalars():
            observation_entry_ids.add(entry.public_id)
    assert analysis.status_code == 200
    assert len(analysis.json()["observations"]) >= 1, "[ANALYSIS ISOLATION] the two aggregate entries must still produce a Signal/Observation"
    for observation in analysis.json()["observations"]:
        assert evidence_response.json()["metric_entry_id"] not in observation["source_metric_entry_ids"], (
            "[ANALYSIS ISOLATION] Evidence-linked MetricEntry must never feed the analysis pipeline"
        )


def test_evidence_creates_a_fresh_metric_entry_never_reuses_an_existing_one(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    first = _create_evidence(fixtures, content_id).json()
    second = _create_evidence(
        fixtures, content_id, client_request_id=next_client_request_id(),
        period_start=_today().isoformat(), period_end=_today().isoformat(), values={"reach": 500, "saves": 12},
    )
    assert second.status_code == 201, second.text
    assert second.json()["metric_entry_id"] != first["metric_entry_id"], "[NO REUSE] every Evidence owns its own fresh MetricEntry"


# --- audit ---------------------------------------------------------------------


def test_evidence_creates_expected_audit_trail(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    created = _create_evidence(fixtures, content_id).json()
    corrected = _correct_evidence(fixtures, content_id, created["id"]).json()

    with Session(get_engine()) as session:
        evidence_row = session.execute(
            select(DistributionMetricEvidence).where(DistributionMetricEvidence.public_id == created["id"])
        ).scalar_one()
        events = session.execute(
            select(AuditEvent).where(AuditEvent.distribution_metric_evidence_id == evidence_row.id)
        ).scalars().all()
        assert {e.event_type for e in events} == {"measurement.distribution_evidence.recorded"}
        assert all(e.actor_type is ActorType.USER and e.actor_user_id is not None for e in events)

        corrected_row = session.execute(
            select(DistributionMetricEvidence).where(DistributionMetricEvidence.public_id == corrected["id"])
        ).scalar_one()
        corrected_events = session.execute(
            select(AuditEvent).where(AuditEvent.distribution_metric_evidence_id == corrected_row.id)
        ).scalars().all()
        assert {e.event_type for e in corrected_events} == {"measurement.distribution_evidence.corrected"}


# --- direct database constraint tests -------------------------------------------


def test_composite_fk_rejects_cross_workspace_and_cross_distribution_rows(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _distributed(fixtures)
    evidence = _create_evidence(fixtures, content_id).json()

    with Session(get_engine()) as session:
        row = session.execute(
            select(DistributionMetricEvidence).where(DistributionMetricEvidence.public_id == evidence["id"])
        ).scalar_one()
        distribution = session.execute(
            select(ContentDistribution).where(ContentDistribution.public_id == evidence["distribution_id"])
        ).scalar_one()
        entry = session.execute(select(MetricEntry).where(MetricEntry.public_id == evidence["metric_entry_id"])).scalar_one()

        # A different Distribution/workspace pair — the composite FK must reject this insert.
        from sqlalchemy.exc import IntegrityError

        bad_row = DistributionMetricEvidence(
            public_id=generate_public_id("DME"),
            workspace_id=row.workspace_id,
            distribution_id=distribution.id,
            metric_entry_id=entry.id,  # already used — also violates UNIQUE(metric_entry_id)
            created_by_user_id=row.created_by_user_id,
        )
        session.add(bad_row)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    # Cross-distribution correction self-FK: build a second, independent
    # Distribution and attempt to supersede across the boundary directly —
    # isolated from the metric_entry_id UNIQUE constraint by giving the bad
    # row its own fresh, otherwise-valid MetricEntry.
    content_id_b = _distributed(fixtures)
    evidence_b = _create_evidence(fixtures, content_id_b).json()
    with Session(get_engine()) as session:
        row_a = session.execute(
            select(DistributionMetricEvidence).where(DistributionMetricEvidence.public_id == evidence["id"])
        ).scalar_one()
        row_b = session.execute(
            select(DistributionMetricEvidence).where(DistributionMetricEvidence.public_id == evidence_b["id"])
        ).scalar_one()
        entry_b = session.execute(select(MetricEntry).where(MetricEntry.id == row_b.metric_entry_id)).scalar_one()
        fresh_entry = MetricEntry(
            public_id=generate_public_id("MET"),
            workspace_id=row_b.workspace_id,
            campaign_id=entry_b.campaign_id,
            period_start=entry_b.period_start,
            period_end=entry_b.period_end,
            channel=entry_b.channel,
            source=entry_b.source,
            client_request_id=generate_public_id("REQ"),
        )
        session.add(fresh_entry)
        session.flush()

        from sqlalchemy.exc import IntegrityError as IntegrityError2

        bad_correction = DistributionMetricEvidence(
            public_id=generate_public_id("DME"),
            workspace_id=row_b.workspace_id,
            distribution_id=row_b.distribution_id,
            metric_entry_id=fresh_entry.id,
            supersedes_evidence_id=row_a.id,  # row_a belongs to a DIFFERENT distribution
            created_by_user_id=row_b.created_by_user_id,
            correction_reason="cross-distribution attempt",
        )
        session.add(bad_correction)
        with pytest.raises(IntegrityError2):
            session.flush()
        session.rollback()


# --- audit failure rollback (service-layer, direct session) --------------------


def _total_count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def _distributed_piece_in_session(session):
    campaign, _run, _stages, plan, item = build_plan_with_item(session)
    user = make_user(session)
    service = ContentService(session)
    brief = service.record_brief(plan_item=item, content_plan=plan, brief="Audit failure evidence")
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
    distribution = session.execute(select(ContentDistribution).where(ContentDistribution.content_piece_id == piece.id)).scalar_one()
    return campaign, distribution, user


def test_create_audit_failure_rolls_back_metric_entry_and_evidence(db_session) -> None:
    campaign, distribution, user = _distributed_piece_in_session(db_session)
    entries_before = _total_count(db_session, MetricEntry)
    evidence_before = _total_count(db_session, DistributionMetricEvidence)
    values_before = _total_count(db_session, MetricValue)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            MeasurementService(db_session).create_distribution_evidence(
                distribution=distribution, campaign=campaign, period_start=_today() - timedelta(days=3),
                period_end=_today(), metric_values={"reach": Decimal("500")},
                client_request_id=next_client_request_id(), source_reference=None, actor_user_id=user.id,
            )

    db_session.rollback()
    assert _total_count(db_session, MetricEntry) == entries_before, "[AUDIT ATOMICITY] no orphan MetricEntry may survive"
    assert _total_count(db_session, DistributionMetricEvidence) == evidence_before, "[AUDIT ATOMICITY] no orphan Evidence may survive"
    assert _total_count(db_session, MetricValue) == values_before, "[AUDIT ATOMICITY] no orphan MetricValue may survive"


def test_correction_audit_failure_leaves_original_current(db_session) -> None:
    campaign, distribution, user = _distributed_piece_in_session(db_session)
    service = MeasurementService(db_session)
    evidence, _created = service.create_distribution_evidence(
        distribution=distribution, campaign=campaign, period_start=_today() - timedelta(days=3), period_end=_today(),
        metric_values={"reach": Decimal("500")}, client_request_id=next_client_request_id(),
        source_reference=None, actor_user_id=user.id,
    )
    db_session.commit()
    entries_before = _total_count(db_session, MetricEntry)
    evidence_before = _total_count(db_session, DistributionMetricEvidence)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            service.create_distribution_evidence_correction(
                distribution=distribution, campaign=campaign, target_evidence_public_id=evidence.public_id,
                period_start=_today() - timedelta(days=2), period_end=_today(),
                metric_values={"reach": Decimal("600")}, client_request_id=next_client_request_id(),
                source_reference=None, correction_reason="audit failure probe", actor_user_id=user.id,
            )

    db_session.rollback()
    assert _total_count(db_session, MetricEntry) == entries_before, "[AUDIT ATOMICITY] no orphan MetricEntry may survive"
    assert _total_count(db_session, DistributionMetricEvidence) == evidence_before, "[AUDIT ATOMICITY] no orphan successor may survive"
    refreshed = db_session.get(DistributionMetricEvidence, evidence.id)
    assert refreshed.supersedes_evidence_id is None, "[AUDIT ATOMICITY] the original Evidence must remain current"


def test_composite_fk_rejects_workspace_mismatch_against_distribution_and_metric_entry(db_session) -> None:
    """MVP-19B §75: direct-DB proof that the composite FKs — not merely
    service discipline — reject a workspace_id that does not match the
    real workspace of the referenced Distribution, and separately one
    that does not match the real workspace of the referenced MetricEntry."""
    from sqlalchemy.exc import IntegrityError as _IntegrityError

    campaign_a, distribution_a, user_a = _distributed_piece_in_session(db_session)
    campaign_b, distribution_b, user_b = _distributed_piece_in_session(db_session)
    assert campaign_a.workspace_id != campaign_b.workspace_id

    service = MeasurementService(db_session)
    evidence_a, _created_a = service.create_distribution_evidence(
        distribution=distribution_a, campaign=campaign_a, period_start=_today() - timedelta(days=3), period_end=_today(),
        metric_values={"reach": Decimal("100")}, client_request_id=next_client_request_id(),
        source_reference=None, actor_user_id=user_a.id,
    )
    entry_a_only = db_session.get(MetricEntry, evidence_a.metric_entry_id)
    evidence_b, _created = service.create_distribution_evidence(
        distribution=distribution_b, campaign=campaign_b, period_start=_today() - timedelta(days=3), period_end=_today(),
        metric_values={"reach": Decimal("500")}, client_request_id=next_client_request_id(),
        source_reference=None, actor_user_id=user_b.id,
    )
    entry_b = db_session.get(MetricEntry, evidence_b.metric_entry_id)

    # workspace_id disagrees with the real Distribution's own workspace.
    bad_vs_distribution = DistributionMetricEvidence(
        public_id=generate_public_id("DME"), workspace_id=campaign_a.workspace_id,
        distribution_id=distribution_b.id, metric_entry_id=entry_b.id, created_by_user_id=user_a.id,
    )
    db_session.add(bad_vs_distribution)
    with pytest.raises(_IntegrityError):
        db_session.flush()
    db_session.rollback()

    # workspace_id agrees with the real Distribution but disagrees with the
    # real MetricEntry's own workspace (entry_a_only belongs to workspace A).
    bad_vs_metric_entry = DistributionMetricEvidence(
        public_id=generate_public_id("DME"), workspace_id=distribution_b.workspace_id,
        distribution_id=distribution_b.id, metric_entry_id=entry_a_only.id, created_by_user_id=user_b.id,
    )
    db_session.add(bad_vs_metric_entry)
    with pytest.raises(_IntegrityError):
        db_session.flush()
    db_session.rollback()
