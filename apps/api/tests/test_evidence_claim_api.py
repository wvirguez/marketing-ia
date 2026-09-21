"""API contract tests for Experiment Evidence Binding (frozen Experiment
Evidence Binding Design Freeze): the exact three-route surface, the read
model and its frozen constants, the frozen error taxonomy and HTTP mapping,
CSRF/authentication/tenancy, MEMBER+ authority, audit, corrections, revocation
(R2) and real-persistence non-effects. All marked `postgres`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.audit.models import ActorType, AuditEvent
from app.persistence.session import get_engine
from app.strategy.models import Experiment
from app.workspaces.models import MembershipRole
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.settingstest import add_member_to_workspace, login_as
from tests.test_execution_authorization_api import _authorize, _revoke, _ready
from tests.test_execution_start_api import _authorized, _start
from tests.test_experiment_api import _post
from tests.test_experiment_definition_api import _delta, _supersede_strategy, _table_counts
from tests.test_measurement_contract_api import _declare_contract, _mc_path, _payload as _contract_payload, _signal

pytestmark = pytest.mark.postgres

SEMANTICS = "PROVENANCE CLAIM ONLY — NOT ELIGIBILITY OR VALIDATION"


def _claims_path(fixtures: dict, experiment_id: str, start_id: str, suffix: str = "") -> str:
    return (
        f"/api/v1/campaigns/{fixtures['campaign_id']}/experiments/{experiment_id}"
        f"/execution-starts/{start_id}/evidence-claims{suffix}"
    )


def _metric(fixtures: dict, *, values: dict | None = None, put: bool = False, **overrides: object):
    body = {
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
        "channel": "Instagram",
        "source": "MANUAL",
        "client_request_id": uuid.uuid4().hex,
        "values": values if values is not None else {"impressions": "1000", "clicks": "50"},
    }
    body.update(overrides)
    path = f"/api/v1/campaigns/{fixtures['campaign_id']}/metrics"
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    response = (fixtures["client"].put if put else fixtures["client"].post)(path, json=body, headers=headers)
    assert response.status_code in (200, 201), response.text
    return response.json()


def _signals(fixtures: dict, experiment_id: str) -> list[dict]:
    return fixtures["client"].get(_mc_path(fixtures, experiment_id)).json()["signals"]


def _started(fixtures: dict, **kwargs) -> dict:
    """A STARTED attempt: {experiment_id, authorization, start_id, signal_id, signals}."""
    experiment_id, _version, authorization = _authorized(fixtures, **kwargs)
    started = _start(fixtures, experiment_id, authorization)
    assert started.status_code == 201, started.text
    signals = _signals(fixtures, experiment_id)
    return {
        "experiment_id": experiment_id,
        "authorization": authorization,
        "start_id": started.json()["execution_start"]["id"],
        "signal_id": signals[0]["id"],
        "signals": signals,
    }


def _body(attempt: dict, entry_id: str, **overrides: object) -> dict:
    body: dict = {
        "client_request_id": uuid.uuid4().hex,
        "required_signal_id": attempt["signal_id"],
        "metric_entry_id": entry_id,
        "metric_name": "clicks",
    }
    body.update(overrides)
    return body


def _claim(fixtures: dict, attempt: dict, body: dict, *, start_id: str | None = None, experiment_id: str | None = None):
    return _post(
        fixtures,
        _claims_path(fixtures, experiment_id or attempt["experiment_id"], start_id or attempt["start_id"]),
        body,
    )


def _dispose(fixtures: dict, attempt: dict, claim_id: str, reason: str | None = "Not intended.", **extra: object):
    body = {} if reason is None else {"reason": reason}
    body.update(extra)
    return _post(fixtures, _claims_path(fixtures, attempt["experiment_id"], attempt["start_id"], f"/{claim_id}/dispose"), body)


def _list(fixtures: dict, attempt: dict, **kwargs):
    return fixtures["client"].get(
        _claims_path(fixtures, kwargs.get("experiment_id") or attempt["experiment_id"], kwargs.get("start_id") or attempt["start_id"])
    )


def _events(experiment_public_id: str, event_type: str) -> list[AuditEvent]:
    with OrmSession(get_engine()) as session:
        experiment = session.execute(select(Experiment).where(Experiment.public_id == experiment_public_id)).scalar_one()
        rows = session.execute(
            select(AuditEvent).where(AuditEvent.experiment_id == experiment.id, AuditEvent.event_type == event_type)
        ).scalars().all()
        session.expunge_all()
        return list(rows)


# --- creation / read model ------------------------------------------------------------------------------


def test_create_returns_201_with_the_frozen_read_model(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    entry = _metric(fixtures)
    response = _claim(fixtures, attempt, _body(attempt, entry["id"]))
    assert response.status_code == 201, response.text
    data = response.json()
    assert set(data) == {
        "id", "experiment_id", "semantics", "scope", "reporter_note", "claimed_by", "created_at", "is_disposed",
        "disposed_at", "disposed_by", "disposal_reason", "authorization", "start", "required_signal", "datum",
        "distribution", "later_correction_exists", "excluded_from_aggregate_and_analysis",
    }
    assert data["id"].startswith("ECL-") and len(data["id"]) == 16
    assert data["semantics"] == SEMANTICS and data["scope"] == "EXPERIMENT_LEVEL"
    assert "not necessarily the member who originally reported" in data["reporter_note"]
    assert data["experiment_id"] == attempt["experiment_id"]
    assert data["claimed_by"].startswith("USR-")
    assert (data["is_disposed"], data["disposed_at"], data["disposed_by"], data["disposal_reason"]) == (False, None, None, None)
    assert data["authorization"] == {"id": attempt["authorization"]["id"], "revoked_at": None, "revoked_reason": None}
    assert set(data["start"]) == {"id", "started_at"} and data["start"]["id"] == attempt["start_id"]
    assert data["required_signal"]["id"] == attempt["signal_id"] and data["required_signal"]["tracking_required"] is False
    assert data["required_signal"]["contract_version"] == 1
    assert data["datum"]["metric_entry_id"] == entry["id"] and data["datum"]["metric_name"] == "clicks"
    assert Decimal(data["datum"]["value"]) == Decimal("50")
    assert (data["datum"]["period_start"], data["datum"]["period_end"]) == ("2026-01-01", "2026-01-31")
    assert data["datum"]["channel"] == "Instagram" and data["datum"]["source"] == "MANUAL"
    assert data["distribution"] is None and data["excluded_from_aggregate_and_analysis"] is False
    assert data["later_correction_exists"] is False


def test_the_response_never_exposes_an_eligibility_validity_or_variant_semantic(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    data = _claim(fixtures, attempt, _body(attempt, _metric(fixtures)["id"])).json()

    def keys(node) -> set[str]:
        if isinstance(node, dict):
            return set(node) | {k for value in node.values() for k in keys(value)}
        if isinstance(node, list):
            return {k for value in node for k in keys(value)}
        return set()

    assert not keys(data) & {
        "eligible", "eligibility", "accepted", "verified", "validated", "qualified", "valid", "validity", "status",
        "is_current", "variant", "variant_id", "assignment", "exposure", "result", "winner", "verdict", "attribution",
        "causality", "sufficient", "comparable", "tracking_valid",
    }
    assert "id" in data and data["scope"] == "EXPERIMENT_LEVEL"


def test_a_matching_replay_is_200_with_the_same_claim_and_no_second_audit_event(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    body = _body(attempt, _metric(fixtures)["id"])
    first = _claim(fixtures, attempt, body)
    second = _claim(fixtures, attempt, body)
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json() == first.json()
    assert len(_events(attempt["experiment_id"], "strategy.evidence_claim.claimed")) == 1


def test_a_replay_after_disposal_and_after_revocation_returns_the_current_state(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    body = _body(attempt, _metric(fixtures)["id"])
    first = _claim(fixtures, attempt, body).json()
    assert _dispose(fixtures, attempt, first["id"]).status_code == 200
    assert _revoke(fixtures, attempt["experiment_id"]).status_code == 200
    replay = _claim(fixtures, attempt, body)
    assert replay.status_code == 200
    assert replay.json()["id"] == first["id"] and replay.json()["is_disposed"] is True  # CURRENT state
    assert replay.json()["authorization"]["revoked_at"] is not None


def test_the_same_key_with_different_material_is_409_idempotency_conflict(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    entry = _metric(fixtures)
    body = _body(attempt, entry["id"])
    assert _claim(fixtures, attempt, body).status_code == 201
    other = _claim(fixtures, attempt, {**body, "metric_name": "impressions"})
    assert other.status_code == 409 and other.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_a_different_key_for_an_active_material_is_409_and_a_disposed_one_allows_a_new_claim(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    entry = _metric(fixtures)
    first = _claim(fixtures, attempt, _body(attempt, entry["id"])).json()
    duplicate = _claim(fixtures, attempt, _body(attempt, entry["id"]))
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "EVIDENCE_CLAIM_ALREADY_ACTIVE"
    assert _dispose(fixtures, attempt, first["id"]).status_code == 200
    again = _claim(fixtures, attempt, _body(attempt, entry["id"]))
    assert again.status_code == 201 and again.json()["id"] != first["id"]


def test_the_same_datum_may_serve_two_signals(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    from tests.test_execution_authorization_api import _experiment
    from tests.test_experiment_definition_api import _declare as _declare_definition, _payload as _definition_payload
    from tests.test_experiment_variant_api import _declare_variant, _vpayload

    _strategy, _hypothesis, experiment_id = _experiment(fixtures)
    version_id = _declare_definition(fixtures, experiment_id, _definition_payload()).json()["id"]
    assert _declare_variant(fixtures, experiment_id, _vpayload(version_id)).status_code == 201
    contract = _declare_contract(
        fixtures, experiment_id, _contract_payload(version_id, signals=[_signal(name="Signal A"), _signal(name="Signal B")])
    )
    assert contract.status_code == 201, contract.text
    authorization = _authorize(fixtures, experiment_id).json()
    started = _start(fixtures, experiment_id, authorization)
    signals = _signals(fixtures, experiment_id)
    attempt = {"experiment_id": experiment_id, "start_id": started.json()["execution_start"]["id"], "signal_id": signals[0]["id"]}
    entry = _metric(fixtures)
    one = _claim(fixtures, attempt, _body(attempt, entry["id"], required_signal_id=signals[0]["id"]))
    two = _claim(fixtures, attempt, _body(attempt, entry["id"], required_signal_id=signals[1]["id"]))
    assert (one.status_code, two.status_code) == (201, 201) and one.json()["id"] != two.json()["id"]


# --- validation / error taxonomy -------------------------------------------------------------------------


def test_a_metric_absent_from_the_entry_is_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    entry = _metric(fixtures)
    for name in ("reach", "Clicks", " clicks"):
        response = _claim(fixtures, attempt, _body(attempt, entry["id"], metric_name=name))
        assert response.status_code == 422, name
        assert response.json()["error"]["code"] == "EVIDENCE_CLAIM_METRIC_NOT_IN_ENTRY"
    assert _list(fixtures, attempt).json()["claims"] == []


def test_a_signal_of_another_pinned_contract_is_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _strategy, _hypothesis, experiment_id, version_id = _ready(fixtures)
    old_signal = _signals(fixtures, experiment_id)[0]["id"]
    assert _authorize(fixtures, experiment_id).status_code == 201
    assert _revoke(fixtures, experiment_id).status_code == 200  # before any Start: the Contract freeze reopens
    revised = _declare_contract(
        fixtures, experiment_id, _contract_payload(version_id, base_version=1, signals=[_signal(name="Second contract signal")])
    )
    assert revised.status_code == 201, revised.text
    authorization = _authorize(fixtures, experiment_id).json()
    started = _start(fixtures, experiment_id, authorization)
    assert started.status_code == 201, started.text
    new_signal = _signals(fixtures, experiment_id)[0]["id"]
    assert new_signal != old_signal
    attempt = {"experiment_id": experiment_id, "start_id": started.json()["execution_start"]["id"], "signal_id": new_signal}
    entry = _metric(fixtures)
    wrong = _claim(fixtures, attempt, _body(attempt, entry["id"], required_signal_id=old_signal))
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "EVIDENCE_CLAIM_SIGNAL_NOT_IN_PINNED_CONTRACT"
    assert _claim(fixtures, attempt, _body(attempt, entry["id"])).status_code == 201


def test_invalid_request_bodies_are_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    entry = _metric(fixtures)
    base = _body(attempt, entry["id"])
    bad_bodies = [
        {**base, "note": "n"}, {**base, "variant_id": "VAR-x"}, {**base, "eligible": True}, {**base, "assignment_id": "x"},
        {**base, "exposure_id": "x"}, {**base, "result": "x"},
        {k: v for k, v in base.items() if k != "client_request_id"}, {k: v for k, v in base.items() if k != "metric_name"},
        {**base, "metric_name": ""}, {**base, "client_request_id": ""}, {**base, "client_request_id": "x" * 101},
        {**base, "metric_name": "x" * 101}, {**base, "metric_name": "a\x00b"},
    ]
    for body in bad_bodies:
        assert _claim(fixtures, attempt, body).status_code == 422, body
    assert _list(fixtures, attempt).json()["claims"] == []


def test_dispose_body_validation(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    claim = _claim(fixtures, attempt, _body(attempt, _metric(fixtures)["id"])).json()
    for body in ({}, {"reason": ""}, {"reason": "   "}, {"reason": "x" * 1001}, {"reason": "ok", "client_request_id": "k"}):
        response = _post(
            fixtures, _claims_path(fixtures, attempt["experiment_id"], attempt["start_id"], f"/{claim['id']}/dispose"), body
        )
        assert response.status_code == 422, body
    assert _list(fixtures, attempt).json()["claims"][0]["is_disposed"] is False


# --- tenancy / non-leaky -------------------------------------------------------------------------------------------


def test_unknown_targets_are_a_non_leaky_403(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    entry = _metric(fixtures)
    for body in (
        _body(attempt, entry["id"], required_signal_id="RSG-TOTALLYFAKE"),
        _body(attempt, "MET-TOTALLYFAKE0"),
    ):
        response = _claim(fixtures, attempt, body)
        assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN", body
    fake_start = _claim(fixtures, attempt, _body(attempt, entry["id"]), start_id="EXS-TOTALLYFAKE")
    fake_experiment = _claim(fixtures, attempt, _body(attempt, entry["id"]), experiment_id="EXP-TOTALLYFAKE0")
    assert fake_start.status_code == 403 and fake_experiment.status_code == 403
    assert _dispose(fixtures, attempt, "ECL-TOTALLYFAKE").status_code == 403
    assert _list(fixtures, attempt, start_id="EXS-TOTALLYFAKE").status_code == 403
    assert _list(fixtures, attempt).json()["claims"] == []


def test_a_cross_campaign_entry_of_the_same_workspace_is_403(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    second = fixtures["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name="Second campaign"), headers={"X-CSRF-Token": fixtures["csrf_token"]}
    ).json()["campaign"]["id"]
    foreign_entry = _metric({**fixtures, "campaign_id": second})
    response = _claim(fixtures, attempt, _body(attempt, foreign_entry["id"]))
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"
    assert _list(fixtures, attempt).json()["claims"] == []


def test_another_workspace_cannot_read_write_or_dispose(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    claim = _claim(fixtures, attempt, _body(attempt, _metric(fixtures)["id"])).json()
    other_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    register_and_get_csrf(other_client, display_name="Other User")
    other_token = other_client.get("/api/v1/auth/csrf").json()["csrf_token"]
    other_campaign = other_client.post(
        "/api/v1/campaigns", json=campaign_payload(name="Other"), headers={"X-CSRF-Token": other_token}
    ).json()["campaign"]["id"]
    other = {**fixtures, "client": other_client, "csrf_token": other_token, "campaign_id": other_campaign}
    foreign_entry = _metric(other)
    assert _claim(other, attempt, _body(attempt, foreign_entry["id"])).status_code == 403  # foreign Experiment path
    assert _list(other, attempt).status_code == 403
    assert _dispose(other, attempt, claim["id"]).status_code == 403
    # ...and the OWNER's own path with the other workspace's entry:
    assert _claim(fixtures, attempt, _body(attempt, foreign_entry["id"])).status_code == 403
    assert _list(fixtures, attempt).json()["claims"][0]["is_disposed"] is False


def test_a_start_of_another_experiment_is_not_reachable_through_this_experiment(campaign_run_client: dict) -> None:
    from tests.test_experiment_api import _experiments_path
    from tests.test_experiment_definition_api import _declare as _declare_definition, _payload as _definition_payload
    from tests.test_experiment_variant_api import _declare_variant, _vpayload

    fixtures = campaign_run_client
    attempt_a = _started(fixtures)
    hypothesis_id = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").json()["hypotheses"][0]["id"]
    created = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "Second comparison."})
    experiment_b = created.json()["id"]
    version_b = _declare_definition(fixtures, experiment_b, _definition_payload()).json()["id"]
    assert _declare_variant(fixtures, experiment_b, _vpayload(version_b)).status_code == 201
    assert _declare_contract(fixtures, experiment_b, _contract_payload(version_b)).status_code == 201
    authorization_b = _authorize(fixtures, experiment_b).json()
    started_b = _start(fixtures, experiment_b, authorization_b).json()["execution_start"]["id"]
    entry = _metric(fixtures)
    crossed = _claim(fixtures, attempt_a, _body(attempt_a, entry["id"]), start_id=started_b)  # B's Start under A's path
    assert crossed.status_code == 403 and crossed.json()["error"]["code"] == "FORBIDDEN"
    foreign_signal = _signals(fixtures, experiment_b)[0]["id"]
    assert _claim(fixtures, attempt_a, _body(attempt_a, entry["id"], required_signal_id=foreign_signal)).status_code == 403
    assert _list(fixtures, attempt_a).json()["claims"] == []


# --- CSRF / authentication / authority ------------------------------------------------------------------------------


def test_mutations_require_csrf_and_authentication_and_reads_require_authentication(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    entry = _metric(fixtures)
    claim = _claim(fixtures, attempt, _body(attempt, entry["id"])).json()
    create_path = _claims_path(fixtures, attempt["experiment_id"], attempt["start_id"])
    dispose_path = _claims_path(fixtures, attempt["experiment_id"], attempt["start_id"], f"/{claim['id']}/dispose")
    no_csrf = fixtures["client"].post(create_path, json=_body(attempt, entry["id"]))
    assert no_csrf.status_code == 403 and no_csrf.json()["error"]["code"] == "CSRF_INVALID"
    no_csrf_dispose = fixtures["client"].post(dispose_path, json={"reason": "x"})
    assert no_csrf_dispose.status_code == 403 and no_csrf_dispose.json()["error"]["code"] == "CSRF_INVALID"
    anon = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    assert anon.post(create_path, json=_body(attempt, entry["id"])).status_code == 401
    assert anon.post(dispose_path, json={"reason": "x"}).status_code == 401
    assert anon.get(create_path).status_code == 401
    assert fixtures["client"].get(create_path).status_code == 200  # GET needs no CSRF token
    assert len(_list(fixtures, attempt).json()["claims"]) == 1


def test_a_member_can_claim_and_dispose_another_members_claim_and_the_actor_is_the_user(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    entry = _metric(fixtures)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}
    owner_claim = _claim(fixtures, attempt, _body(attempt, entry["id"])).json()
    member_claim = _claim(member_fixtures, attempt, _body(attempt, entry["id"], metric_name="impressions"))
    assert member_claim.status_code == 201
    assert member_claim.json()["claimed_by"] != owner_claim["claimed_by"]
    disposed = _dispose(member_fixtures, attempt, owner_claim["id"], reason="Member disposes owner's claim.")
    assert disposed.status_code == 200
    assert disposed.json()["disposed_by"] == member_claim.json()["claimed_by"]
    assert disposed.json()["claimed_by"] == owner_claim["claimed_by"]  # the original assertion is never rewritten
    created = _events(attempt["experiment_id"], "strategy.evidence_claim.claimed")
    disposals = _events(attempt["experiment_id"], "strategy.evidence_claim.disposed")
    assert len(created) == 2 and len(disposals) == 1
    assert all(e.actor_type == ActorType.USER and e.actor_user_id is not None for e in created + disposals)


# --- revocation (R2), strategy independence, reauthorization ---------------------------------------------------------


def test_a_late_claim_after_revocation_is_allowed_and_revoked_at_is_literal(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    entry = _metric(fixtures)
    assert _revoke(fixtures, attempt["experiment_id"], "Paused.").status_code == 200
    response = _claim(fixtures, attempt, _body(attempt, entry["id"]))
    assert response.status_code == 201, response.text
    authorization = response.json()["authorization"]
    assert authorization["revoked_at"] is not None and authorization["revoked_reason"] == "Paused."


def test_a_claim_is_allowed_after_a_later_strategy_revision(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, _version, authorization = _authorized(fixtures)
    started = _start(fixtures, experiment_id, authorization)
    signals = _signals(fixtures, experiment_id)
    strategy_id = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").json()["strategy"]["id"]
    _supersede_strategy(fixtures, strategy_id)  # no current-Strategy check exists for claims
    attempt = {"experiment_id": experiment_id, "start_id": started.json()["execution_start"]["id"], "signal_id": signals[0]["id"]}
    assert _claim(fixtures, attempt, _body(attempt, _metric(fixtures)["id"])).status_code == 201
    assert _list(fixtures, attempt).status_code == 200  # and the history stays readable


def test_claims_of_two_attempts_are_listed_separately_and_never_migrate(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt_a = _started(fixtures)
    entry = _metric(fixtures)
    claim_a = _claim(fixtures, attempt_a, _body(attempt_a, entry["id"])).json()
    assert _revoke(fixtures, attempt_a["experiment_id"]).status_code == 200
    authorization_b = _authorize(fixtures, attempt_a["experiment_id"]).json()
    started_b = _start(fixtures, attempt_a["experiment_id"], authorization_b)
    assert started_b.status_code == 201, started_b.text
    attempt_b = {**attempt_a, "start_id": started_b.json()["execution_start"]["id"], "authorization": authorization_b}
    claim_b = _claim(fixtures, attempt_b, _body(attempt_b, entry["id"]))  # same datum, same signal, a DIFFERENT attempt
    assert claim_b.status_code == 201 and claim_b.json()["id"] != claim_a["id"]
    assert [c["id"] for c in _list(fixtures, attempt_a).json()["claims"]] == [claim_a["id"]]
    assert [c["id"] for c in _list(fixtures, attempt_b).json()["claims"]] == [claim_b.json()["id"]]
    assert claim_b.json()["authorization"]["id"] == authorization_b["id"] != claim_a["authorization"]["id"]


# --- corrections (C1) --------------------------------------------------------------------------------------------------


def test_a_metric_correction_never_retargets_an_existing_claim(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    original = _metric(fixtures, values={"clicks": "50"})
    claim = _claim(fixtures, attempt, _body(attempt, original["id"])).json()
    assert claim["later_correction_exists"] is False
    correction = _metric(fixtures, values={"clicks": "75"}, put=True)  # same grouping => the new current row
    assert correction["id"] != original["id"]
    listed = _list(fixtures, attempt).json()["claims"]
    assert len(listed) == 1  # the correction created no claim
    assert listed[0]["datum"]["metric_entry_id"] == original["id"]  # never retargeted
    assert Decimal(listed[0]["datum"]["value"]) == Decimal("50")  # the historical datum, not the correction
    assert listed[0]["later_correction_exists"] is True  # a read-time observation only
    fresh = _claim(fixtures, attempt, _body(attempt, correction["id"]))  # the corrected datum needs a NEW claim
    assert fresh.status_code == 201 and fresh.json()["later_correction_exists"] is False
    late = _claim(fixtures, attempt, _body(attempt, original["id"], required_signal_id=attempt["signal_id"]))
    assert late.status_code == 409  # (already active) — C1 governs a NEW claim on a non-current datum, tested next


def test_a_new_claim_on_an_already_non_current_datum_is_allowed(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    original = _metric(fixtures, values={"clicks": "50"})
    _metric(fixtures, values={"clicks": "75"}, put=True)  # `original` is now non-current
    response = _claim(fixtures, attempt, _body(attempt, original["id"]))
    assert response.status_code == 201 and response.json()["later_correction_exists"] is True


# --- list / dispose read-your-writes ----------------------------------------------------------------------------------------


def test_the_list_is_ascending_and_keeps_disposed_claims(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    entry = _metric(fixtures)
    assert _list(fixtures, attempt).json() == {
        "experiment_id": attempt["experiment_id"], "start_id": attempt["start_id"], "claims": [],
    }
    first = _claim(fixtures, attempt, _body(attempt, entry["id"], metric_name="clicks")).json()
    second = _claim(fixtures, attempt, _body(attempt, entry["id"], metric_name="impressions")).json()
    disposed = _dispose(fixtures, attempt, first["id"], reason="Wrong metric.")
    assert disposed.status_code == 200
    body = disposed.json()
    assert body["is_disposed"] is True and body["disposal_reason"] == "Wrong metric."
    assert body["disposed_at"] is not None and body["disposed_by"].startswith("USR-")
    claims = _list(fixtures, attempt).json()["claims"]
    assert [c["id"] for c in claims] == [first["id"], second["id"]]  # ascending, disposed claim still readable
    assert [c["is_disposed"] for c in claims] == [True, False]
    assert claims[0]["claimed_by"] == first["claimed_by"] and claims[0]["semantics"] == SEMANTICS


def test_a_second_dispose_is_409_and_the_first_disposal_is_untouched(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    claim = _claim(fixtures, attempt, _body(attempt, _metric(fixtures)["id"])).json()
    first = _dispose(fixtures, attempt, claim["id"], reason="First.").json()
    second = _dispose(fixtures, attempt, claim["id"], reason="Second.")
    assert second.status_code == 409 and second.json()["error"]["code"] == "EVIDENCE_CLAIM_ALREADY_DISPOSED"
    listed = _list(fixtures, attempt).json()["claims"][0]
    # Compare INSTANTS: the same instant may render with a different UTC offset per code path (the DB session
    # timezone is never fixed — EEB-DISC-OBS-1, deliberately not repaired here).
    assert listed["disposal_reason"] == "First."
    assert datetime.fromisoformat(listed["disposed_at"]) == datetime.fromisoformat(first["disposed_at"])
    assert len(_events(attempt["experiment_id"], "strategy.evidence_claim.disposed")) == 1


# --- audit + non-effects + surface --------------------------------------------------------------------------------------------


def test_create_and_dispose_write_only_the_claim_and_audit_rows(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    entry = _metric(fixtures)
    before = _table_counts()
    claim = _claim(fixtures, attempt, _body(attempt, entry["id"])).json()
    after_create = _table_counts()
    # No Measurement run, Observation, Signal, AnalysisResult, Learning, Tracking, Commercial, Hypothesis, Variant row.
    assert _delta(before, after_create) == {"experiment_evidence_claims": 1, "audit_events": 1}
    assert _dispose(fixtures, attempt, claim["id"]).status_code == 200
    assert _delta(after_create, _table_counts()) == {"audit_events": 1}
    (created,) = _events(attempt["experiment_id"], "strategy.evidence_claim.claimed")
    (disposed,) = _events(attempt["experiment_id"], "strategy.evidence_claim.disposed")
    assert (created.previous_state, created.new_state) == (None, f"claimed:{claim['id']}")
    assert (disposed.previous_state, disposed.new_state) == (f"claimed:{claim['id']}", f"disposed:{claim['id']}")
    assert created.strategy_id is None and created.hypothesis_id is None and created.metric_entry_id is None
    assert disposed.strategy_id is None and disposed.hypothesis_id is None and disposed.metric_entry_id is None


def test_failed_and_replayed_requests_write_nothing(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _started(fixtures)
    entry = _metric(fixtures)
    body = _body(attempt, entry["id"])
    claim = _claim(fixtures, attempt, body).json()
    before = _table_counts()
    assert _claim(fixtures, attempt, body).status_code == 200  # replay
    assert _claim(fixtures, attempt, _body(attempt, entry["id"])).status_code == 409  # active duplicate
    assert _claim(fixtures, attempt, _body(attempt, entry["id"], metric_name="reach")).status_code == 422
    assert _claim(fixtures, attempt, _body(attempt, "MET-TOTALLYFAKE0")).status_code == 403
    assert _dispose(fixtures, attempt, claim["id"], reason=None).status_code == 422
    assert _delta(before, _table_counts()) == {}
    assert len(_events(attempt["experiment_id"], "strategy.evidence_claim.claimed")) == 1


def test_only_the_three_frozen_routes_exist(campaign_run_client: dict) -> None:
    schema = campaign_run_client["client"].app.openapi()
    routes = {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        if "evidence-claims" in path
        for method in operations
    }
    prefix = "/api/v1/campaigns/{campaign_public_id}/experiments/{experiment_public_id}/execution-starts/{start_public_id}/evidence-claims"
    assert routes == {
        ("POST", prefix),
        ("GET", prefix),
        ("POST", prefix + "/{claim_public_id}/dispose"),
    }  # no get-one, no list-by-signal, no list-by-Experiment, no PATCH/PUT/DELETE
