"""API contract, tenancy, idempotency, correction, and read/history tests
for the governed CommercialOutcome surface (MVP-36, frozen by
MVP-36A/-R1). All marked `postgres`."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.commercial.models import CommercialOutcome
from app.commercial.service import EVENT_OUTCOME_CORRECTED, EVENT_OUTCOME_RECORDED

from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.test_content_api import (
    _advance_to_ready_for_review,
    _lifecycle_path,
    _post,
    _record_content_piece,
    _under_review_approval_id,
    campaign_client_with_stages,
)

pytestmark = pytest.mark.postgres


def _outcomes_path(fixtures: dict) -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/commercial-outcomes"


def _correction_path(fixtures: dict, outcome_id: str) -> str:
    return f"{_outcomes_path(fixtures)}/{outcome_id}/corrections"


def _headers(fixtures: dict) -> dict:
    return {"X-CSRF-Token": fixtures["csrf_token"]}


def _create(fixtures: dict, **overrides) -> "TestClient.Response":  # type: ignore[name-defined]
    body = {
        "outcome_type": "lead",
        "occurred_at": "2026-01-15T12:00:00Z",
        "client_request_id": str(uuid.uuid4()),
    }
    body.update(overrides)
    return fixtures["client"].post(_outcomes_path(fixtures), json=body, headers=_headers(fixtures))


def _correct(fixtures: dict, outcome_id: str, **overrides) -> "TestClient.Response":  # type: ignore[name-defined]
    body = {
        "outcome_type": "lead",
        "occurred_at": "2026-01-15T12:00:00Z",
        "client_request_id": str(uuid.uuid4()),
        "correction_reason": "Corrected value.",
    }
    body.update(overrides)
    return fixtures["client"].post(_correction_path(fixtures, outcome_id), json=body, headers=_headers(fixtures))


def _distributed_content(fixtures: dict) -> str:
    """Drives a Piece through create -> approval -> mark-ready-for-distribution,
    returning the ContentDistribution's own public id (READY is sufficient
    — CommercialOutcome's optional provenance has no DISTRIBUTED
    precondition, unlike Measurement Evidence)."""
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    approval_id = _under_review_approval_id(fixtures, content_id)
    decision = _post(fixtures, _lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/decision"), {"decision": "APPROVED"})
    assert decision.status_code == 200, decision.text
    ready = _post(fixtures, _lifecycle_path(fixtures, content_id, "/distribution/mark-ready-for-distribution"))
    assert ready.status_code == 201, ready.text
    return ready.json()["distribution"]["id"]


# --- route surface / basic create -------------------------------------------


def test_only_get_post_routes_exist(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    assert fixtures["client"].get(_outcomes_path(fixtures)).status_code == 200
    assert fixtures["client"].put(_outcomes_path(fixtures), json={}).status_code == 405
    assert fixtures["client"].delete(_outcomes_path(fixtures)).status_code == 405


def test_empty_list_for_campaign_with_no_outcomes(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    assert fixtures["client"].get(_outcomes_path(fixtures)).json() == []


def test_create_returns_201_and_frozen_shape(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _create(
        fixtures, outcome_type="purchase", quantity=3, monetary_value="97.50", currency="usd",
        external_reference="order-123",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"].startswith("OUT")
    assert body["campaign_id"] == fixtures["campaign_id"]
    assert body["outcome_type"] == "purchase"
    assert body["quantity"] == 3
    assert float(body["monetary_value"]) == 97.50
    assert body["currency"] == "USD"  # normalized from lowercase input
    assert body["external_reference"] == "order-123"
    assert body["content_distribution_id"] is None
    assert body["is_current"] is True
    assert body["supersedes_outcome_id"] is None
    assert body["corrected_by_commercial_outcome_id"] is None
    assert body["correction_reason"] is None
    assert body["occurred_at"].startswith("2026-01-15")


def test_two_outcomes_same_campaign_both_succeed_0_to_n(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    first = _create(fixtures, client_request_id=str(uuid.uuid4()))
    second = _create(fixtures, client_request_id=str(uuid.uuid4()))
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    listing = fixtures["client"].get(_outcomes_path(fixtures)).json()
    assert len(listing) == 2


def test_identical_business_fields_different_keys_both_succeed(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    same_fields = {"outcome_type": "lead", "occurred_at": "2026-02-01T00:00:00Z"}
    first = _create(fixtures, client_request_id=str(uuid.uuid4()), **same_fields)
    second = _create(fixtures, client_request_id=str(uuid.uuid4()), **same_fields)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


# --- field validation --------------------------------------------------------


def test_extra_field_is_forbidden(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _create(fixtures, experiment_id="EXP-123")
    assert response.status_code == 422


def test_blank_outcome_type_returns_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    assert _create(fixtures, outcome_type="   ").status_code == 422


def test_missing_occurred_at_returns_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    body = {"outcome_type": "lead", "client_request_id": str(uuid.uuid4())}
    response = fixtures["client"].post(_outcomes_path(fixtures), json=body, headers=_headers(fixtures))
    assert response.status_code == 422


def test_quantity_zero_rejected(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    assert _create(fixtures, quantity=0).status_code == 422


def test_quantity_negative_rejected(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    assert _create(fixtures, quantity=-1).status_code == 422


def test_monetary_value_without_currency_returns_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    assert _create(fixtures, monetary_value="10.00").status_code == 422


def test_currency_without_monetary_value_returns_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    assert _create(fixtures, currency="USD").status_code == 422


def test_negative_monetary_value_returns_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    assert _create(fixtures, monetary_value="-1.00", currency="USD").status_code == 422


def test_zero_monetary_value_allowed(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _create(fixtures, monetary_value="0", currency="USD")
    assert response.status_code == 201, response.text
    assert float(response.json()["monetary_value"]) == 0.0


def test_malformed_currency_returns_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    assert _create(fixtures, monetary_value="10.00", currency="US").status_code == 422


def test_money_decimal_precision_preserved(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _create(fixtures, monetary_value="1234.5678", currency="EUR")
    assert response.status_code == 201, response.text
    assert response.json()["monetary_value"] == "1234.5678"


# --- authority / CSRF --------------------------------------------------------


def test_create_without_csrf_token_is_rejected(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(
        _outcomes_path(fixtures), json={"outcome_type": "lead", "occurred_at": "2026-01-15T12:00:00Z", "client_request_id": str(uuid.uuid4())}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


def test_unauthenticated_create_is_rejected(auth_client: TestClient) -> None:
    csrf = register_and_get_csrf(auth_client)
    campaign_id = auth_client.post("/api/v1/campaigns", json=campaign_payload(), headers={"X-CSRF-Token": csrf}).json()["campaign"]["id"]
    unauthenticated = TestClient(auth_client.app, raise_server_exceptions=False)
    response = unauthenticated.post(
        f"/api/v1/campaigns/{campaign_id}/commercial-outcomes",
        json={"outcome_type": "lead", "occurred_at": "2026-01-15T12:00:00Z", "client_request_id": str(uuid.uuid4())},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 401


def test_member_can_create_outcome(campaign_run_client: dict) -> None:
    """MEMBER+ authority (MVP-36A §N) — no OWNER/ADMIN gate, unlike
    Content Approval decision / Distribution record-distributed."""
    fixtures = campaign_run_client
    assert _create(fixtures).status_code == 201


# --- tenancy: Distribution provenance ----------------------------------------


def test_create_with_same_campaign_distribution_accepted(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    distribution_id = _distributed_content(fixtures)
    response = _create(fixtures, content_distribution_id=distribution_id)
    assert response.status_code == 201, response.text
    assert response.json()["content_distribution_id"] == distribution_id


def test_create_with_unknown_distribution_returns_403_not_404(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _create(fixtures, content_distribution_id="DST-DOESNOTEXIST")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_create_with_cross_campaign_distribution_returns_403(campaign_client_with_stages: dict) -> None:
    """A ContentDistribution that genuinely exists, but under a DIFFERENT
    Campaign in the SAME workspace, must be rejected exactly like an
    unknown one — same-workspace alone never proves it belongs to the
    Campaign named in the URL (MVP-36A §5/§R)."""
    fixtures = campaign_client_with_stages
    distribution_id = _distributed_content(fixtures)

    other_campaign = fixtures["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name="Other Campaign"), headers={"X-CSRF-Token": fixtures["csrf_token"]}
    ).json()
    other_fixtures = {"client": fixtures["client"], "csrf_token": fixtures["csrf_token"], "campaign_id": other_campaign["campaign"]["id"]}
    response = _create(other_fixtures, content_distribution_id=distribution_id)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_create_with_cross_workspace_distribution_returns_403(campaign_client_with_stages: dict, auth_client: TestClient) -> None:
    fixtures = campaign_client_with_stages
    distribution_id = _distributed_content(fixtures)

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    csrf_b = register_and_get_csrf(client_b, display_name="Outcome User B")
    campaign_b_id = client_b.post("/api/v1/campaigns", json=campaign_payload(name="Workspace B"), headers={"X-CSRF-Token": csrf_b}).json()["campaign"]["id"]
    fixtures_b = {"client": client_b, "csrf_token": csrf_b, "campaign_id": campaign_b_id}
    response = _create(fixtures_b, content_distribution_id=distribution_id)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


# --- idempotency: create ------------------------------------------------------


def test_exact_replay_returns_200_and_original_row(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    key = str(uuid.uuid4())
    fields = {"outcome_type": "purchase", "monetary_value": "50.00", "currency": "USD", "occurred_at": "2026-01-15T12:00:00Z"}
    first = _create(fixtures, client_request_id=key, **fields)
    assert first.status_code == 201, first.text
    replay = _create(fixtures, client_request_id=key, **fields)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"]
    listing = fixtures["client"].get(_outcomes_path(fixtures)).json()
    assert len(listing) == 1  # no duplicate row


def test_same_key_different_payload_returns_409_idempotency_conflict(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    key = str(uuid.uuid4())
    first = _create(fixtures, client_request_id=key, outcome_type="purchase")
    assert first.status_code == 201
    conflict = _create(fixtures, client_request_id=key, outcome_type="lead")  # different outcome_type
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    listing = fixtures["client"].get(_outcomes_path(fixtures)).json()
    assert len(listing) == 1  # conflicting attempt never persisted


# --- correction ---------------------------------------------------------------


def test_correction_full_state_replaces_all_fields(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    created = _create(fixtures, outcome_type="lead", quantity=1).json()
    corrected = _correct(fixtures, created["id"], outcome_type="purchase", quantity=5, monetary_value="10.00", currency="USD")
    assert corrected.status_code == 201, corrected.text
    body = corrected.json()
    assert body["outcome_type"] == "purchase"
    assert body["quantity"] == 5
    assert body["supersedes_outcome_id"] == created["id"]
    assert body["is_current"] is True
    assert body["correction_reason"] == "Corrected value."


def test_correction_inherits_distribution_and_cannot_change_it(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    distribution_id = _distributed_content(fixtures)
    created = _create(fixtures, content_distribution_id=distribution_id).json()

    # content_distribution_id is structurally not a correction field at all.
    rejected = _correct(fixtures, created["id"], content_distribution_id=distribution_id)
    assert rejected.status_code == 422  # extra=forbid

    corrected = _correct(fixtures, created["id"], outcome_type="purchase")
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["content_distribution_id"] == distribution_id


def test_correction_reason_required(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    created = _create(fixtures).json()
    body = {"outcome_type": "purchase", "occurred_at": "2026-01-15T12:00:00Z", "client_request_id": str(uuid.uuid4())}
    response = fixtures["client"].post(_correction_path(fixtures, created["id"]), json=body, headers=_headers(fixtures))
    assert response.status_code == 422


def test_original_row_remains_immutable_after_correction(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    created = _create(fixtures, outcome_type="lead").json()
    corrected = _correct(fixtures, created["id"], outcome_type="purchase").json()

    listing = {item["id"]: item for item in fixtures["client"].get(_outcomes_path(fixtures)).json()}
    assert listing[created["id"]]["outcome_type"] == "lead"  # never mutated
    assert listing[created["id"]]["is_current"] is False
    assert listing[created["id"]]["corrected_by_commercial_outcome_id"] == corrected["id"]
    assert listing[corrected["id"]]["is_current"] is True


def test_tip_only_second_correction_of_same_original_returns_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    created = _create(fixtures).json()
    first_correction = _correct(fixtures, created["id"], outcome_type="purchase")
    assert first_correction.status_code == 201

    stale = _correct(fixtures, created["id"], outcome_type="another attempt")
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "COMMERCIAL_OUTCOME_CORRECTION_TARGET_STALE"


def test_linear_chain_o1_o2_o3(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    o1 = _create(fixtures, outcome_type="lead").json()
    o2 = _correct(fixtures, o1["id"], outcome_type="qualified lead").json()
    o3 = _correct(fixtures, o2["id"], outcome_type="purchase")
    assert o3.status_code == 201, o3.text
    o3_body = o3.json()

    listing = {item["id"]: item for item in fixtures["client"].get(_outcomes_path(fixtures)).json()}
    assert listing[o1["id"]]["is_current"] is False
    assert listing[o1["id"]]["corrected_by_commercial_outcome_id"] == o2["id"]
    assert listing[o2["id"]]["is_current"] is False
    assert listing[o2["id"]]["corrected_by_commercial_outcome_id"] == o3_body["id"]
    assert listing[o3_body["id"]]["is_current"] is True
    assert listing[o3_body["id"]]["supersedes_outcome_id"] == o2["id"]


def test_correction_of_o1_after_o2_exists_returns_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    o1 = _create(fixtures).json()
    _correct(fixtures, o1["id"], outcome_type="second state")
    attempt = _correct(fixtures, o1["id"], outcome_type="third attempt")
    assert attempt.status_code == 409
    assert attempt.json()["error"]["code"] == "COMMERCIAL_OUTCOME_CORRECTION_TARGET_STALE"


# --- correction idempotency ---------------------------------------------------


def test_correction_exact_replay_returns_200(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    created = _create(fixtures).json()
    key = str(uuid.uuid4())
    fields = {"outcome_type": "purchase", "occurred_at": "2026-01-15T12:00:00Z", "correction_reason": "Fixed value."}
    first = _correct(fixtures, created["id"], client_request_id=key, **fields)
    assert first.status_code == 201, first.text
    replay = _correct(fixtures, created["id"], client_request_id=key, **fields)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"]

    listing = fixtures["client"].get(_outcomes_path(fixtures)).json()
    assert len(listing) == 2  # original + exactly one correction, never a duplicate


def test_correction_key_reused_against_different_target_is_conflict(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    o1 = _create(fixtures).json()
    o2 = _create(fixtures, client_request_id=str(uuid.uuid4())).json()
    key = str(uuid.uuid4())
    first = _correct(fixtures, o1["id"], client_request_id=key)
    assert first.status_code == 201
    conflict = _correct(fixtures, o2["id"], client_request_id=key)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_correction_key_reused_with_different_payload_is_conflict(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    created = _create(fixtures).json()
    key = str(uuid.uuid4())
    first = _correct(fixtures, created["id"], client_request_id=key, outcome_type="purchase")
    assert first.status_code == 201
    conflict = _correct(fixtures, created["id"], client_request_id=key, outcome_type="something else entirely")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_idempotency_resolved_before_stale_target_check(campaign_run_client: dict) -> None:
    """MVP-36A-R1 §7/§10's mandatory ordering: replaying a prior
    correction must return 200 with that correction's own row, even
    though a LATER, different correction has since superseded it
    further — never a stale-target 409."""
    fixtures = campaign_run_client
    o1 = _create(fixtures).json()
    first_key = str(uuid.uuid4())
    first_fields = {"outcome_type": "qualified lead", "occurred_at": "2026-01-15T12:00:00Z", "correction_reason": "First correction."}
    o2 = _correct(fixtures, o1["id"], client_request_id=first_key, **first_fields)
    assert o2.status_code == 201
    o2_id = o2.json()["id"]

    # A later, different correction supersedes O2.
    later = _correct(fixtures, o2_id, client_request_id=str(uuid.uuid4()), outcome_type="purchase")
    assert later.status_code == 201

    # Replaying the FIRST correction (targeting O1, now two steps stale)
    # must still succeed as an idempotent replay, not a stale-target 409.
    replay = _correct(fixtures, o1["id"], client_request_id=first_key, **first_fields)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == o2_id


# --- read / history -----------------------------------------------------------


def test_get_returns_current_and_historical_with_pointers(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    standalone = _create(fixtures, client_request_id=str(uuid.uuid4())).json()
    o1 = _create(fixtures, client_request_id=str(uuid.uuid4())).json()
    o2 = _correct(fixtures, o1["id"], outcome_type="corrected").json()

    listing = {item["id"]: item for item in fixtures["client"].get(_outcomes_path(fixtures)).json()}
    assert len(listing) == 3
    assert listing[standalone["id"]]["is_current"] is True
    assert listing[standalone["id"]]["supersedes_outcome_id"] is None
    assert listing[standalone["id"]]["corrected_by_commercial_outcome_id"] is None
    assert listing[o1["id"]]["is_current"] is False
    assert listing[o1["id"]]["corrected_by_commercial_outcome_id"] == o2["id"]
    assert listing[o2["id"]]["is_current"] is True
    assert listing[o2["id"]]["supersedes_outcome_id"] == o1["id"]


def test_get_ordering_is_deterministic_by_occurred_at_desc(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    earlier = _create(fixtures, client_request_id=str(uuid.uuid4()), occurred_at="2026-01-01T00:00:00Z").json()
    later = _create(fixtures, client_request_id=str(uuid.uuid4()), occurred_at="2026-02-01T00:00:00Z").json()
    listing = fixtures["client"].get(_outcomes_path(fixtures)).json()
    ids = [item["id"] for item in listing]
    assert ids.index(later["id"]) < ids.index(earlier["id"])


# --- non-leaky correction target resolution -----------------------------------


def test_correction_of_unknown_outcome_returns_403_not_404(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _correct(fixtures, "OUT-DOESNOTEXIST")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_correction_rejects_cross_workspace_target(auth_client: TestClient) -> None:
    client_a = auth_client
    csrf_a = register_and_get_csrf(client_a, display_name="Outcome Correction A")
    campaign_a_id = client_a.post("/api/v1/campaigns", json=campaign_payload(name="Corr A"), headers={"X-CSRF-Token": csrf_a}).json()["campaign"]["id"]

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    csrf_b = register_and_get_csrf(client_b, display_name="Outcome Correction B")
    campaign_b_id = client_b.post("/api/v1/campaigns", json=campaign_payload(name="Corr B"), headers={"X-CSRF-Token": csrf_b}).json()["campaign"]["id"]
    outcome_b = client_b.post(
        f"/api/v1/campaigns/{campaign_b_id}/commercial-outcomes",
        json={"outcome_type": "lead", "occurred_at": "2026-01-15T12:00:00Z", "client_request_id": str(uuid.uuid4())},
        headers={"X-CSRF-Token": csrf_b},
    ).json()

    response = client_a.post(
        f"/api/v1/campaigns/{campaign_a_id}/commercial-outcomes/{outcome_b['id']}/corrections",
        json={"outcome_type": "x", "occurred_at": "2026-01-15T12:00:00Z", "client_request_id": str(uuid.uuid4()), "correction_reason": "x"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


# --- firewalls: no attribution/experiment/tracking/objective fields ----------


def test_no_attribution_or_causal_fields_accepted(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    for field in ("caused_by", "attributed_to", "conversion_source", "winning_content", "causal_experiment", "roas", "variant_id"):
        response = _create(fixtures, **{field: "x"})
        assert response.status_code == 422, f"{field} should be rejected"


def test_no_experiment_objective_offer_or_tracking_linkage_in_response(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    body = _create(fixtures).json()
    forbidden_keys = {
        "experiment_id", "variant_id", "commercial_objective_id", "offer_id",
        "tracking_requirement_id", "attribution", "causal", "winner",
    }
    assert forbidden_keys.isdisjoint(body.keys())


# --- MVP-36B-R1: numeric range / scale / timezone contract (D2, D3, D4) ------


def _domain_counts(engine) -> tuple[int, int]:
    """(CommercialOutcome rows, successful-domain AuditEvents) across the
    whole test database — used as a before/after delta, never an absolute."""
    with Session(engine) as session:
        rows = session.scalar(select(func.count()).select_from(CommercialOutcome))
        events = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type.in_((EVENT_OUTCOME_RECORDED, EVENT_OUTCOME_CORRECTED)))
        )
    return rows, events


def _persisted(engine, public_id: str) -> CommercialOutcome:
    with Session(engine) as session:
        row = session.scalar(select(CommercialOutcome).where(CommercialOutcome.public_id == public_id))
        session.expunge(row)
        return row


_NAIVE = "2026-03-01T10:00:00"
_INVALID_NUMERIC_TEMPORAL_INPUTS = [
    pytest.param({"monetary_value": "1.23456", "currency": "USD"}, id="money-scale-5"),
    pytest.param({"monetary_value": "0.00001", "currency": "USD"}, id="money-scale-5-tiny"),
    pytest.param({"monetary_value": "50.00000", "currency": "USD"}, id="money-scale-5-trailing-zero"),
    pytest.param({"monetary_value": "100000000", "currency": "USD"}, id="money-overflow-1e8"),
    pytest.param({"monetary_value": "123456789.00", "currency": "USD"}, id="money-overflow-9-digits"),
    pytest.param({"monetary_value": "99999999.99991", "currency": "USD"}, id="money-max-plus-sub-scale"),
    pytest.param({"monetary_value": "-0.0001", "currency": "USD"}, id="money-negative"),
    pytest.param({"quantity": 0}, id="quantity-zero"),
    pytest.param({"quantity": -1}, id="quantity-negative"),
    pytest.param({"quantity": 2147483648}, id="quantity-int4-plus-one"),
    pytest.param({"quantity": 2**40}, id="quantity-2-pow-40"),
    pytest.param({"occurred_at": _NAIVE}, id="occurred-at-naive"),
    pytest.param({"occurred_at": "2026-03-01"}, id="occurred-at-date-only"),
]


@pytest.mark.parametrize("bad", _INVALID_NUMERIC_TEMPORAL_INPUTS)
def test_create_rejects_out_of_contract_input_with_422_and_zero_mutation(
    campaign_run_client: dict, postgres_engine, bad: dict
) -> None:
    fixtures = campaign_run_client
    before = _domain_counts(postgres_engine)
    response = _create(fixtures, **bad)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] != "IDEMPOTENCY_KEY_CONFLICT"
    assert _domain_counts(postgres_engine) == before
    assert fixtures["client"].get(_outcomes_path(fixtures)).json() == []


@pytest.mark.parametrize("bad", _INVALID_NUMERIC_TEMPORAL_INPUTS)
def test_correction_rejects_out_of_contract_input_original_stays_current(
    campaign_run_client: dict, postgres_engine, bad: dict
) -> None:
    fixtures = campaign_run_client
    original = _create(fixtures).json()
    before = _domain_counts(postgres_engine)
    response = _correct(fixtures, original["id"], **bad)
    assert response.status_code == 422, response.text
    assert _domain_counts(postgres_engine) == before  # no successor row, no AuditEvent
    listing = fixtures["client"].get(_outcomes_path(fixtures)).json()
    assert [(o["id"], o["is_current"], o["corrected_by_commercial_outcome_id"]) for o in listing] == [
        (original["id"], True, None)
    ]


def test_scale_5_money_is_never_rounded_nor_replayable_as_a_false_conflict(
    campaign_run_client: dict, postgres_engine
) -> None:
    """D2: the pre-repair defect returned 201 echoing 1.23456 while the DB
    persisted 1.2346, and the exact replay then produced a false 409."""
    fixtures = campaign_run_client
    key = str(uuid.uuid4())
    before = _domain_counts(postgres_engine)
    for _ in range(2):
        response = _create(fixtures, client_request_id=key, monetary_value="1.23456", currency="USD")
        assert response.status_code == 422
    assert _domain_counts(postgres_engine) == before


@pytest.mark.parametrize("value", ["0", "0.1", "1.2345", "99999999.9999"])
def test_valid_money_is_accepted_and_persisted_exactly(
    campaign_run_client: dict, postgres_engine, value: str
) -> None:
    fixtures = campaign_run_client
    key = str(uuid.uuid4())
    response = _create(fixtures, client_request_id=key, monetary_value=value, currency="USD")
    assert response.status_code == 201, response.text
    persisted = _persisted(postgres_engine, response.json()["id"])
    assert persisted.monetary_value == Decimal(value)  # exact, never rounded
    assert Decimal(response.json()["monetary_value"]) == persisted.monetary_value  # response == DB
    replay = _create(fixtures, client_request_id=key, monetary_value=value, currency="USD")
    assert replay.status_code == 200, replay.text  # no false 409 at any boundary
    assert replay.json()["id"] == response.json()["id"]


@pytest.mark.parametrize("value", [1, 2147483647])
def test_quantity_int4_boundaries_are_accepted_and_replayable(
    campaign_run_client: dict, postgres_engine, value: int
) -> None:
    fixtures = campaign_run_client
    key = str(uuid.uuid4())
    response = _create(fixtures, client_request_id=key, quantity=value)
    assert response.status_code == 201, response.text
    assert response.json()["quantity"] == value
    assert _persisted(postgres_engine, response.json()["id"]).quantity == value
    assert _create(fixtures, client_request_id=key, quantity=value).status_code == 200


@pytest.mark.parametrize(
    "occurred_at",
    ["2026-03-01T10:00:00Z", "2026-03-01T10:00:00+00:00", "2026-03-01T05:00:00-05:00"],
)
def test_aware_occurred_at_is_accepted(campaign_run_client: dict, occurred_at: str) -> None:
    fixtures = campaign_run_client
    response = _create(fixtures, occurred_at=occurred_at)
    assert response.status_code == 201, response.text


def test_timezone_equivalent_replay_returns_200_same_row_no_new_audit(
    campaign_run_client: dict, postgres_engine
) -> None:
    fixtures = campaign_run_client
    key = str(uuid.uuid4())
    first = _create(fixtures, client_request_id=key, occurred_at="2026-03-01T10:00:00Z")
    assert first.status_code == 201, first.text
    after_first = _domain_counts(postgres_engine)
    for equivalent in ("2026-03-01T05:00:00-05:00", "2026-03-01T10:00:00+00:00", "2026-03-01T10:00:00.000000Z"):
        replay = _create(fixtures, client_request_id=key, occurred_at=equivalent)
        assert replay.status_code == 200, (equivalent, replay.text)
        assert replay.json()["id"] == first.json()["id"]
    assert _domain_counts(postgres_engine) == after_first  # no new row, no new AuditEvent
    # A genuinely different instant is still a material change.
    different = _create(fixtures, client_request_id=key, occurred_at="2026-03-01T10:00:01Z")
    assert different.status_code == 409
    assert different.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_exact_replay_regression_one_row_one_recorded_audit_same_id(
    campaign_run_client: dict, postgres_engine
) -> None:
    fixtures = campaign_run_client
    key = str(uuid.uuid4())
    before = _domain_counts(postgres_engine)
    fields = {"outcome_type": "purchase", "monetary_value": "50.00", "currency": "USD", "occurred_at": "2026-01-15T12:00:00Z"}
    first = _create(fixtures, client_request_id=key, **fields)
    replay = _create(fixtures, client_request_id=key, **fields)
    assert (first.status_code, replay.status_code) == (201, 200)
    assert replay.json()["id"] == first.json()["id"]
    after = _domain_counts(postgres_engine)
    assert (after[0] - before[0], after[1] - before[1]) == (1, 1)


def test_currency_case_replay_is_the_same_canonical_payload(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    key = str(uuid.uuid4())
    first = _create(fixtures, client_request_id=key, monetary_value="50", currency="usd")
    assert first.status_code == 201, first.text
    for variant in ("USD", " usd ", "Usd"):
        replay = _create(fixtures, client_request_id=key, monetary_value="50", currency=variant)
        assert replay.status_code == 200, (variant, replay.text)
        assert replay.json()["id"] == first.json()["id"]


def test_decimal_representations_replay_as_the_same_canonical_value(
    campaign_run_client: dict, postgres_engine
) -> None:
    fixtures = campaign_run_client
    key = str(uuid.uuid4())
    first = _create(fixtures, client_request_id=key, monetary_value="50", currency="USD")
    assert first.status_code == 201, first.text
    assert _persisted(postgres_engine, first.json()["id"]).monetary_value == Decimal("50.0000")
    for representation in ("50.00", "50.0000", 50, 50.0):
        replay = _create(fixtures, client_request_id=key, monetary_value=representation, currency="USD")
        assert replay.status_code == 200, (representation, replay.text)
        assert replay.json()["id"] == first.json()["id"]
    assert len(fixtures["client"].get(_outcomes_path(fixtures)).json()) == 1


def test_correction_boundary_values_accepted_and_timezone_equivalent_replay(
    campaign_run_client: dict, postgres_engine
) -> None:
    fixtures = campaign_run_client
    original = _create(fixtures).json()
    key = str(uuid.uuid4())
    fields = {"quantity": 2147483647, "monetary_value": "99999999.9999", "currency": "USD"}
    first = _correct(fixtures, original["id"], client_request_id=key, occurred_at="2026-03-01T10:00:00Z", **fields)
    assert first.status_code == 201, first.text
    persisted = _persisted(postgres_engine, first.json()["id"])
    assert (persisted.quantity, persisted.monetary_value) == (2147483647, Decimal("99999999.9999"))
    after_first = _domain_counts(postgres_engine)
    replay = _correct(fixtures, original["id"], client_request_id=key, occurred_at="2026-03-01T05:00:00-05:00", **fields)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert _domain_counts(postgres_engine) == after_first
