"""API contract, tenancy, and campaign-scope tests for the Learning
surface (BACKEND-14 §S/§T, Governance Freeze-R GF-D26). All marked
`postgres`.

No public write endpoint exists for LearningCandidate, so test data is
recorded via ``LearningService``/``MeasurementService`` against the live
app's own engine (the same pattern ``tests/test_assets_api.py``'s helper
uses), then read back through the real, authenticated HTTP client.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as OrmSession

from app.campaigns.repository import CampaignRepository
from app.learning.models import LearningCandidateStatus, StrategicRecommendationDecision
from app.learning.service import LearningService
from app.measurement.models import MetricSource
from app.measurement.service import MeasurementService
from app.persistence.session import get_engine
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.measurementtest import default_metric_values, default_period, next_client_request_id

pytestmark = pytest.mark.postgres

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _record_recommendation(campaign_public_id: str, *, validated: bool = True) -> tuple[str, str]:
    """Records one MetricEntry -> Observation -> Signal -> AnalysisResult
    -> LearningCandidate (optionally driven to VALIDATED) -> Strategic
    Recommendation Candidate for the given Campaign, via a genuinely
    separate, immediately-committed session bound to the app's own
    engine. Returns ``(learning_candidate_public_id, recommendation_public_id)``."""
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(campaign_public_id)
        measurement = MeasurementService(session)
        period_start, period_end = default_period()
        entry = measurement.record_metric_entry(
            campaign=campaign, period_start=period_start, period_end=period_end, channel="Instagram",
            source=MetricSource.MANUAL, client_request_id=next_client_request_id(), metric_values=default_metric_values(),
        )
        observation = measurement.record_observation(campaign=campaign, metric_entries=[entry], metric_name="CTR", value=Decimal("5.0"))
        signal = measurement.record_signal(campaign=campaign, observations=[observation], summary="CTR trending up.")
        analysis_result = measurement.record_analysis_result(campaign=campaign, signals=[signal], summary="Durable improvement.")

        learning = LearningService(session)
        candidate = learning.record_learning_candidate(analysis_result=analysis_result, summary="Shorter hooks win.")
        if not validated:
            return candidate.public_id, None
        learning.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.PROVISIONAL)
        learning.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.VALIDATION_PENDING)
        learning.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.VALIDATED)
        recommendation = learning.record_strategic_recommendation_candidate(learning_candidate=candidate, summary="Shift toward shorter hooks.")
        return candidate.public_id, recommendation.public_id


def _learning_path(fixtures: dict, suffix: str = "") -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/learning{suffix}"


# --- route surface -------------------------------------------------------


def test_only_get_and_patch_routes_exist(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _lrn, src = _record_recommendation(fixtures["campaign_id"])
    path = _learning_path(fixtures)
    assert fixtures["client"].get(path).status_code == 200
    assert fixtures["client"].post(path, json={}).status_code == 405
    assert fixtures["client"].put(path, json={}).status_code == 405
    assert fixtures["client"].delete(path).status_code == 405
    # No LearningCandidate maturity PATCH route.
    assert fixtures["client"].patch(path, json={}).status_code == 405


def test_no_public_post_route_for_recommendation(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(
        _learning_path(fixtures, "/SRC-FAKE0000000"), json={"decision": "ACCEPTED"},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 405


# --- GET response shape --------------------------------------------------


def test_empty_learning_response_for_campaign_with_no_analysis(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].get(_learning_path(fixtures))
    assert response.status_code == 200
    assert response.json() == {"learning_candidates": [], "strategic_recommendation_candidates": []}


def test_get_returns_frozen_shape(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id, src_id = _record_recommendation(fixtures["campaign_id"])

    body = fixtures["client"].get(_learning_path(fixtures)).json()
    assert len(body["learning_candidates"]) == 1
    candidate = body["learning_candidates"][0]
    assert candidate["id"] == lrn_id
    assert candidate["analysis_result_id"].startswith("ANL-")
    assert candidate["status"] == "VALIDATED"
    assert candidate["summary"]
    assert "created_at" in candidate
    assert set(candidate.keys()) == {"id", "analysis_result_id", "status", "summary", "created_at"}

    assert len(body["strategic_recommendation_candidates"]) == 1
    recommendation = body["strategic_recommendation_candidates"][0]
    assert recommendation["id"] == src_id
    assert recommendation["learning_candidate_id"] == lrn_id
    assert recommendation["decision"] is None
    assert recommendation["decided_at"] is None
    assert set(recommendation.keys()) == {"id", "learning_candidate_id", "summary", "decision", "created_at", "decided_at"}


# --- PATCH contract --------------------------------------------------------


def test_patch_accepts_recommendation(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _lrn_id, src_id = _record_recommendation(fixtures["campaign_id"])

    response = fixtures["client"].patch(
        _learning_path(fixtures, f"/{src_id}"), json={"decision": "ACCEPTED"}, headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == src_id
    assert body["decision"] == "ACCEPTED"
    assert body["decided_at"] is not None


def test_patch_already_decided_returns_conflict(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _lrn_id, src_id = _record_recommendation(fixtures["campaign_id"])
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    fixtures["client"].patch(_learning_path(fixtures, f"/{src_id}"), json={"decision": "ACCEPTED"}, headers=headers)

    response = fixtures["client"].patch(_learning_path(fixtures, f"/{src_id}"), json={"decision": "REJECTED"}, headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RECOMMENDATION_ALREADY_DECIDED"


def test_patch_invalid_decision_enum_rejected(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _lrn_id, src_id = _record_recommendation(fixtures["campaign_id"])
    response = fixtures["client"].patch(
        _learning_path(fixtures, f"/{src_id}"), json={"decision": "MAYBE"}, headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 422


def test_patch_unknown_recommendation_is_forbidden(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].patch(
        _learning_path(fixtures, "/SRC-TOTALLYFAKE0"), json={"decision": "ACCEPTED"}, headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


# --- security --------------------------------------------------------------


def test_no_raw_uuid_or_forbidden_fields_in_response(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _record_recommendation(fixtures["campaign_id"])
    text = fixtures["client"].get(_learning_path(fixtures)).text
    assert not _UUID_RE.search(text), "learning response leaked a raw UUID"
    lowered = text.lower()
    for forbidden in ("workspace_id", "confidence", "reasoning", "chain_of_thought", "agent_id"):
        assert forbidden not in lowered


def test_learning_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.get("/api/v1/campaigns/CMP-FAKE00000000/learning")
    assert response.status_code == 401


# --- tenancy / campaign-scope resource integrity (GF-D26) -----------------


def test_tenant_a_cannot_read_tenant_bs_learning(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()
    _lrn, src_id = _record_recommendation(body_b["campaign"]["id"])

    response = client_a.get(f"/api/v1/campaigns/{body_b['campaign']['id']}/learning")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_wrong_workspace_patch_is_forbidden_and_does_not_mutate(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()
    _lrn, src_id = _record_recommendation(body_b["campaign"]["id"])

    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_a = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A"), headers={"X-CSRF-Token": csrf_a}).json()
    response = client_a.patch(
        f"/api/v1/campaigns/{body_a['campaign']['id']}/learning/{src_id}",
        json={"decision": "ACCEPTED"}, headers={"X-CSRF-Token": csrf_a},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_same_workspace_different_campaign_get_and_patch_are_isolated(campaign_run_client: dict) -> None:
    """MANDATORY two-campaign/same-workspace fixture (BACKEND-14 §28):
    Workspace W has Campaign A and Campaign B; Campaign B's Learning
    artifacts must never appear in Campaign A's GET, and a PATCH through
    Campaign A's URL targeting Campaign B's recommendation must be
    rejected exactly like a nonexistent recommendation, with no mutation."""
    fixtures = campaign_run_client  # Campaign A
    lrn_a, src_a = _record_recommendation(fixtures["campaign_id"])

    csrf = fixtures["csrf_token"]
    body_b = fixtures["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name="Campaign B"), headers={"X-CSRF-Token": csrf}
    ).json()
    campaign_b_id = body_b["campaign"]["id"]
    lrn_b, src_b = _record_recommendation(campaign_b_id)

    # GET Campaign A must include only A's artifacts.
    body = fixtures["client"].get(_learning_path(fixtures)).json()
    assert [c["id"] for c in body["learning_candidates"]] == [lrn_a]
    assert [r["id"] for r in body["strategic_recommendation_candidates"]] == [src_a]

    # GET Campaign B must include only B's artifacts.
    body_b_get = fixtures["client"].get(f"/api/v1/campaigns/{campaign_b_id}/learning").json()
    assert [c["id"] for c in body_b_get["learning_candidates"]] == [lrn_b]
    assert [r["id"] for r in body_b_get["strategic_recommendation_candidates"]] == [src_b]

    # PATCH /campaigns/A/learning/SRC-B must fail non-leakily, no mutation.
    response = fixtures["client"].patch(
        _learning_path(fixtures, f"/{src_b}"), json={"decision": "ACCEPTED"}, headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"

    # Confirm zero mutation: SRC-B is still undecided and can still be
    # accepted correctly through its OWN campaign's URL.
    correct_response = fixtures["client"].patch(
        f"/api/v1/campaigns/{campaign_b_id}/learning/{src_b}", json={"decision": "ACCEPTED"}, headers={"X-CSRF-Token": csrf},
    )
    assert correct_response.status_code == 200
    assert correct_response.json()["decision"] == "ACCEPTED"
