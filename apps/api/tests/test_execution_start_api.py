"""API contract tests for Governed Execution Start (frozen Governed Execution
Start Design Freeze): the one new command surface, the embedded read model,
the frozen error taxonomy and HTTP mapping, CSRF/authentication/tenancy,
MEMBER+ authority, audit, and real-persistence non-effects. All marked
`postgres`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

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
from tests.test_execution_authorization_api import (
    _authorize,
    _body,
    _current,
    _ea_path,
    _history,
    _ready,
    _revoke,
)
from tests.test_experiment_api import _post
from tests.test_experiment_definition_api import _delta, _supersede_strategy, _table_counts
from tests.test_experiment_variant_api import _declare_variant, _vpayload
from tests.test_measurement_contract_api import _declare_contract, _payload as _contract_payload, _signal

pytestmark = pytest.mark.postgres


def _start_path(fixtures: dict, experiment_id: str, authorization_id: str, campaign_id: str | None = None) -> str:
    return (
        f"/api/v1/campaigns/{campaign_id or fixtures['campaign_id']}/experiments/{experiment_id}"
        f"/execution-authorizations/{authorization_id}/start"
    )


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _authorization_created_at(authorization: dict) -> datetime:
    return datetime.fromisoformat(authorization["created_at"])


def _start_body(authorization: dict, *, seconds: int = 1, **overrides: object) -> dict:
    body: dict = {
        "client_request_id": uuid.uuid4().hex,
        "started_at": _iso(_authorization_created_at(authorization) + timedelta(seconds=seconds)),
    }
    body.update(overrides)
    return body


def _start(fixtures: dict, experiment_id: str, authorization: dict, body: dict | None = None):
    return _post(
        fixtures,
        _start_path(fixtures, experiment_id, authorization["id"]),
        body if body is not None else _start_body(authorization),
    )


def _authorized(fixtures: dict, **kwargs) -> tuple[str, str, dict]:
    """(experiment_id, EXD id, authorization dict)."""
    _, _, experiment_id, version_id = _ready(fixtures, **kwargs)
    response = _authorize(fixtures, experiment_id)
    assert response.status_code == 201, response.text
    return experiment_id, version_id, response.json()


def _start_audit(experiment_public_id: str) -> list[AuditEvent]:
    with OrmSession(get_engine()) as session:
        experiment = session.execute(select(Experiment).where(Experiment.public_id == experiment_public_id)).scalar_one()
        rows = session.execute(
            select(AuditEvent).where(
                AuditEvent.experiment_id == experiment.id, AuditEvent.event_type == "strategy.execution_start.attested"
            )
        ).scalars().all()
        session.expunge_all()
        return list(rows)


# --- creation / shape ------------------------------------------------------------------------------------


def test_start_returns_201_with_the_embedded_read_model(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, _version, authorization = _authorized(fixtures)
    body = _start_body(authorization, seconds=30)
    response = _start(fixtures, experiment_id, authorization, body)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["id"] == authorization["id"] and data["active"] is True
    start = data["execution_start"]
    assert set(start) == {"id", "started_at", "created_at"}
    assert start["id"].startswith("EXS-") and len(start["id"]) == 16
    assert datetime.fromisoformat(start["started_at"]) == datetime.fromisoformat(body["started_at"])
    assert start["created_at"] != start["started_at"]  # attested vs server-recorded: two distinct values


def test_the_response_never_exposes_a_derived_execution_state(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, _version, authorization = _authorized(fixtures)
    data = _start(fixtures, experiment_id, authorization).json()
    text = str(data).lower()
    for word in ("executing", "completed", "successful", "winner", "exposure", "assigned", "validated"):
        assert word not in text, word
    assert "valid" not in set(data) and "executing" not in set(data) and "status" not in set(data)
    assert not {"note", "external_reference", "ended_at", "status", "active_start"} & set(data["execution_start"])


def test_a_matching_replay_returns_200_with_the_same_record_and_no_second_audit_event(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, _version, authorization = _authorized(fixtures)
    body = _start_body(authorization)
    first = _start(fixtures, experiment_id, authorization, body)
    second = _start(fixtures, experiment_id, authorization, body)
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json()["execution_start"] == first.json()["execution_start"]
    assert len(_start_audit(experiment_id)) == 1


def test_a_matching_replay_after_revocation_returns_200(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, _version, authorization = _authorized(fixtures)
    body = _start_body(authorization)
    first = _start(fixtures, experiment_id, authorization, body)
    assert _revoke(fixtures, experiment_id).status_code == 200
    replay = _start(fixtures, experiment_id, authorization, body)
    assert replay.status_code == 200 and replay.json()["execution_start"] == first.json()["execution_start"]
    assert replay.json()["active"] is False


# --- read model (current + history) ------------------------------------------------------------------------------


def test_current_and_history_embed_the_start_and_history_keeps_it_after_revocation(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, _version, authorization = _authorized(fixtures)
    assert _current(fixtures, experiment_id).json()["execution_start"] is None  # not yet attested
    start = _start(fixtures, experiment_id, authorization).json()["execution_start"]
    assert _current(fixtures, experiment_id).json()["execution_start"] == start
    assert _history(fixtures, experiment_id).json()["authorizations"][0]["execution_start"] == start
    assert _revoke(fixtures, experiment_id).status_code == 200
    # EXSTART-DF-OBS-5: the current read is null once the started Authorization is revoked; history keeps the fact.
    assert _current(fixtures, experiment_id).json() is None
    history = _history(fixtures, experiment_id).json()["authorizations"]
    assert history[0]["execution_start"] == start and history[0]["active"] is False


# --- frozen error taxonomy ---------------------------------------------------------------------------------------------


def test_a_revoked_authorization_is_409_not_active(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, _version, authorization = _authorized(fixtures)
    assert _revoke(fixtures, experiment_id).status_code == 200
    response = _start(fixtures, experiment_id, authorization)
    assert response.status_code == 409 and response.json()["error"]["code"] == "EXECUTION_START_AUTHORIZATION_NOT_ACTIVE"


def test_a_different_key_after_a_start_is_409_already_started(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, _version, authorization = _authorized(fixtures)
    assert _start(fixtures, experiment_id, authorization).status_code == 201
    response = _start(fixtures, experiment_id, authorization)  # fresh random key
    assert response.status_code == 409 and response.json()["error"]["code"] == "EXECUTION_START_ALREADY_STARTED"


def test_the_same_key_with_a_different_started_at_is_409_idempotency_conflict(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, _version, authorization = _authorized(fixtures)
    first = _start_body(authorization, seconds=5)
    assert _start(fixtures, experiment_id, authorization, first).status_code == 201
    other = {**first, "started_at": _start_body(authorization, seconds=6)["started_at"]}
    response = _start(fixtures, experiment_id, authorization, other)
    assert response.status_code == 409 and response.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_invalid_start_times_are_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, _version, authorization = _authorized(fixtures)
    created = _authorization_created_at(authorization)
    before = _start(fixtures, experiment_id, authorization, _start_body(authorization, seconds=-1))
    assert before.status_code == 422 and before.json()["error"]["code"] == "EXECUTION_START_TIME_INVALID"
    future = _start(
        fixtures, experiment_id, authorization,
        {"client_request_id": uuid.uuid4().hex, "started_at": _iso(datetime.now(timezone.utc) + timedelta(hours=1))},
    )
    assert future.status_code == 422 and future.json()["error"]["code"] == "EXECUTION_START_TIME_INVALID"
    assert created is not None
    assert _current(fixtures, experiment_id).json()["execution_start"] is None  # nothing was written


def test_a_stale_authorization_is_409_and_reauthorizing_resolves_it(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, version_id, authorization = _authorized(fixtures)
    assert _declare_variant(fixtures, experiment_id, _vpayload(version_id, label="Late")).status_code == 201
    response = _start(fixtures, experiment_id, authorization)
    assert response.status_code == 409 and response.json()["error"]["code"] == "EXECUTION_START_AUTHORIZATION_STALE"
    fresh = _authorize(fixtures, experiment_id)  # unstarted active -> auto-supersession, snapshot now complete
    assert fresh.status_code == 201 and len(fresh.json()["variants"]) == 2
    assert _start(fixtures, experiment_id, fresh.json()).status_code == 201


def test_the_contract_is_permanently_frozen_by_a_start_even_after_revocation(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, version_id, authorization = _authorized(fixtures)
    assert _start(fixtures, experiment_id, authorization).status_code == 201
    revision = _contract_payload(version_id, base_version=1, measurement_window_days=21)
    frozen = _declare_contract(fixtures, experiment_id, revision)
    assert frozen.status_code == 409 and frozen.json()["error"]["code"] == "MEASUREMENT_CONTRACT_FROZEN_BY_EXECUTION_START"
    assert _revoke(fixtures, experiment_id).status_code == 200
    still = _declare_contract(fixtures, experiment_id, {**revision, "client_request_id": uuid.uuid4().hex})
    assert still.status_code == 409 and still.json()["error"]["code"] == "MEASUREMENT_CONTRACT_FROZEN_BY_EXECUTION_START"


def test_an_unstarted_revoked_authorization_still_allows_a_contract_revision(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, version_id, _authorization = _authorized(fixtures)
    assert _revoke(fixtures, experiment_id).status_code == 200
    revision = _contract_payload(version_id, base_version=1, measurement_window_days=21, signals=[_signal(name="Revised")])
    assert _declare_contract(fixtures, experiment_id, revision).status_code == 201


def test_variant_declaration_is_permanently_refused_after_a_start(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, version_id, authorization = _authorized(fixtures)
    assert _start(fixtures, experiment_id, authorization).status_code == 201
    refused = _declare_variant(fixtures, experiment_id, _vpayload(version_id, label="Post"))
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "EXPERIMENT_VARIANT_FROZEN_BY_EXECUTION_START"
    assert _revoke(fixtures, experiment_id).status_code == 200
    again = _declare_variant(fixtures, experiment_id, _vpayload(version_id, label="Post"))
    assert again.status_code == 409 and again.json()["error"]["code"] == "EXPERIMENT_VARIANT_FROZEN_BY_EXECUTION_START"


def test_authorize_is_refused_while_the_active_authorization_has_started(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, _version, authorization = _authorized(fixtures)
    assert _start(fixtures, experiment_id, authorization).status_code == 201
    refused = _authorize(fixtures, experiment_id, _body(allocation_design="Changed mid-run."))
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "EXECUTION_AUTHORIZATION_ACTIVE_STARTED"
    assert _current(fixtures, experiment_id).json()["id"] == authorization["id"]  # not revoked, not superseded
    assert _revoke(fixtures, experiment_id).status_code == 200
    reauthorized = _authorize(fixtures, experiment_id, _body(allocation_design="Changed after explicit revoke."))
    assert reauthorized.status_code == 201
    second = _start(fixtures, experiment_id, reauthorized.json())
    assert second.status_code == 201 and second.json()["execution_start"]["id"] != authorization["id"]
    history = _history(fixtures, experiment_id).json()["authorizations"]
    assert [a["execution_start"] is not None for a in history] == [True, True]  # independent provenance each


# --- request validation --------------------------------------------------------------------------------------------------


def test_a_naive_or_missing_started_at_and_extra_fields_are_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, _version, authorization = _authorized(fixtures)
    path = _start_path(fixtures, experiment_id, authorization["id"])
    good = _start_body(authorization)
    for bad in (
        {**good, "started_at": "2026-01-01T10:00:00"},  # naive
        {"client_request_id": good["client_request_id"]},  # missing started_at
        {"started_at": good["started_at"]},  # missing key
        {**good, "note": "x"}, {**good, "external_reference": "x"}, {**good, "unit_reference": "u"},
        {**good, "cohort": "c"}, {**good, "variant_id": "VAR-X"}, {**good, "status": "STARTED"},
    ):
        response = _post(fixtures, path, bad)
        assert response.status_code == 422, bad


# --- CSRF / authentication / tenancy ---------------------------------------------------------------------------------------


def test_start_requires_csrf_and_authentication(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, _version, authorization = _authorized(fixtures)
    path = _start_path(fixtures, experiment_id, authorization["id"])
    no_csrf = fixtures["client"].post(path, json=_start_body(authorization))
    assert no_csrf.status_code == 403 and no_csrf.json()["error"]["code"] == "CSRF_INVALID"
    anon = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    assert anon.post(path, json=_start_body(authorization)).status_code == 401


def test_unknown_and_cross_scope_targets_are_a_non_leaky_403(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, _version, authorization = _authorized(fixtures)
    fake_auth = _post(fixtures, _start_path(fixtures, experiment_id, "EXA-TOTALLYFAKE"), _start_body(authorization))
    assert fake_auth.status_code == 403 and fake_auth.json()["error"]["code"] == "FORBIDDEN"
    fake_exp = _post(fixtures, _start_path(fixtures, "EXP-TOTALLYFAKE0", authorization["id"]), _start_body(authorization))
    assert fake_exp.status_code == 403

    other_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    register_and_get_csrf(other_client, display_name="Other User")
    other_token = other_client.get("/api/v1/auth/csrf").json()["csrf_token"]
    other_campaign = other_client.post(
        "/api/v1/campaigns", json=campaign_payload(name="Other"), headers={"X-CSRF-Token": other_token}
    ).json()["campaign"]["id"]
    other = {**fixtures, "client": other_client, "csrf_token": other_token, "campaign_id": other_campaign}
    cross = _post(other, _start_path(other, experiment_id, authorization["id"]), _start_body(authorization))
    assert cross.status_code == 403 and cross.json()["error"]["code"] == "FORBIDDEN"
    assert _current(fixtures, experiment_id).json()["execution_start"] is None  # nothing was written


def test_an_authorization_of_another_experiment_is_not_reachable_through_this_experiment(campaign_run_client: dict) -> None:
    from tests.test_experiment_api import _experiments_path

    fixtures = campaign_run_client
    _strategy, hypothesis_id, experiment_a, version_a = _ready(fixtures)
    authorization_a = _authorize(fixtures, experiment_a).json()
    # A second Experiment under the SAME Hypothesis/Campaign, fully authorizable in its own right.
    created = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "Second comparison."})
    assert created.status_code == 201, created.text
    experiment_b = created.json()["id"]
    from tests.test_experiment_definition_api import _declare as _declare_definition, _payload as _definition_payload

    version_b = _declare_definition(fixtures, experiment_b, _definition_payload()).json()["id"]
    assert _declare_variant(fixtures, experiment_b, _vpayload(version_b)).status_code == 201
    assert _declare_contract(fixtures, experiment_b, _contract_payload(version_b)).status_code == 201
    authorization_b = _authorize(fixtures, experiment_b).json()
    crossed = _post(fixtures, _start_path(fixtures, experiment_a, authorization_b["id"]), _start_body(authorization_b))
    assert crossed.status_code == 403 and crossed.json()["error"]["code"] == "FORBIDDEN"
    assert version_a and authorization_a["id"] != authorization_b["id"]
    assert _current(fixtures, experiment_b).json()["execution_start"] is None


def test_a_member_can_start_and_the_audit_actor_is_the_user(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, _version, authorization = _authorized(fixtures)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}
    assert _start(member_fixtures, experiment_id, authorization).status_code == 201
    events = _start_audit(experiment_id)
    assert len(events) == 1 and events[0].actor_type == ActorType.USER and events[0].actor_user_id is not None


# --- Strategy independence (S1) ---------------------------------------------------------------------------------------------


def test_the_start_succeeds_after_a_later_strategy_revision_while_authorize_stays_stale(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id, _hyp, experiment_id, _version = _ready(fixtures)
    authorization = _authorize(fixtures, experiment_id).json()
    _supersede_strategy(fixtures, strategy_id)
    stale = _authorize(fixtures, experiment_id, _body(allocation_design="After revision."))
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "EXECUTION_AUTHORIZATION_STRATEGY_STALE"
    started = _start(fixtures, experiment_id, authorization)
    assert started.status_code == 201 and started.json()["active"] is True


# --- audit + non-effects --------------------------------------------------------------------------------------------------------


def test_one_audit_event_and_no_other_table_changes(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, _version, authorization = _authorized(fixtures)
    before = _table_counts()
    assert _start(fixtures, experiment_id, authorization).status_code == 201
    delta = _delta(before, _table_counts())
    # Content, Distribution, TrackingRequirement, CommercialOutcome, Metric/Measurement, Learning,
    # StrategicDecision, StrategyRevision, Hypothesis and every Experiment-definition table are untouched.
    assert delta == {"execution_start_attestations": 1, "audit_events": 1}
    events = _start_audit(experiment_id)
    assert len(events) == 1
    assert events[0].new_state == f"attested:{authorization['id']}" and events[0].previous_state is None


def test_failed_and_replayed_requests_write_nothing(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id, _version, authorization = _authorized(fixtures)
    body = _start_body(authorization)
    assert _start(fixtures, experiment_id, authorization, body).status_code == 201
    before = _table_counts()
    assert _start(fixtures, experiment_id, authorization, body).status_code == 200  # replay
    assert _start(fixtures, experiment_id, authorization).status_code == 409  # already started
    assert _start(fixtures, experiment_id, authorization, _start_body(authorization, seconds=-5)).status_code in (409, 422)
    assert _delta(before, _table_counts()) == {}
    assert len(_start_audit(experiment_id)) == 1
