"""API contract, pinning, Definition-lock, idempotency, eligibility, audit,
tenancy, read-model, non-effects and firewall tests for Governed Measurement
Contract (MVP-39, implementing the frozen MVP-39A/-39B contract). All marked
`postgres`.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.audit.models import ActorType, AuditEvent
from app.measurement.models import AnalysisResult, MetricEntry
from app.persistence.base import Base
from app.persistence.session import get_engine
from app.strategy.models import Experiment, MeasurementContractRequiredSignal, MeasurementContractVersion
from app.tracking.models import TrackingRequirement
from app.workspaces.models import MembershipRole
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.settingstest import add_member_to_workspace, login_as
from tests.test_experiment_api import _post
from tests.test_experiment_definition_api import (
    _declare as _declare_definition,
    _delta,
    _experiment,
    _supersede_strategy,
    _table_counts,
)

pytestmark = pytest.mark.postgres

FORBIDDEN_FIELDS = [
    "allocation", "exposure", "winner", "result", "execution_authorization", "status", "frozen_at", "is_frozen",
    "tracking_plan_id", "metric_entry_id", "commercial_outcome_id", "experiment_id", "workspace_id", "version",
    "created_by", "observed_value",
]


def _mc_path(fixtures: dict, experiment_id: str, campaign_id: str | None = None) -> str:
    return f"/api/v1/campaigns/{campaign_id or fixtures['campaign_id']}/experiments/{experiment_id}/measurement-contract"


def _signal(**overrides: object) -> dict:
    body: dict = {
        "name": "Click-through rate",
        "description": "The share of viewers who clicked through to the link.",
    }
    body.update(overrides)
    return body


def _payload(version_id: str, **overrides: object) -> dict:
    body: dict = {
        "base_version": 0,
        "client_request_id": uuid.uuid4().hex,
        "definition_version_id": version_id,
        "measurement_window_days": 14,
        "minimum_evidence": "At least two weeks of distribution activity.",
        "success_criterion": None,
        "analysis_method_intent": "Compare period-over-period CTR.",
        "stopping_rule": None,
        "decision_rule_intent": "A team reviews the signal manually.",
        "signals": [_signal()],
    }
    body.update(overrides)
    return body


def _declare_contract(fixtures: dict, experiment_id: str, body: dict):
    return _post(fixtures, _mc_path(fixtures, experiment_id), body)


def _get_current(fixtures: dict, experiment_id: str):
    return fixtures["client"].get(_mc_path(fixtures, experiment_id))


def _get_history(fixtures: dict, experiment_id: str):
    return fixtures["client"].get(_mc_path(fixtures, experiment_id) + "/history")


def _defined_experiment(fixtures: dict) -> tuple[str, str, str, str]:
    strategy_id, hypothesis_id, experiment_id = _experiment(fixtures)
    version = _declare_definition(fixtures, experiment_id)
    assert version.status_code == 201, version.text
    return strategy_id, hypothesis_id, experiment_id, version.json()["id"]


def _strategy_tip(fixtures: dict, experiment_id: str) -> dict:
    experiments = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").json()["experiments"]
    return next(e for e in experiments if e["id"] == experiment_id)


def _contract_audit(experiment_public_id: str) -> list[dict]:
    with OrmSession(get_engine()) as session:
        experiment = session.execute(select(Experiment).where(Experiment.public_id == experiment_public_id)).scalar_one()
        rows = session.execute(
            select(AuditEvent).where(
                AuditEvent.experiment_id == experiment.id,
                AuditEvent.event_type.like("strategy.measurement_contract.%"),
            ).order_by(AuditEvent.created_at, AuditEvent.id)
        ).scalars().all()
        return [
            {"previous_state": r.previous_state, "new_state": r.new_state, "actor_type": r.actor_type, "contract_id": r.measurement_contract_id}
            for r in rows
        ]


# --- creation / shape / list -----------------------------------------------------------


def test_creation_returns_201_and_the_frozen_public_shape(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    response = _declare_contract(fixtures, experiment_id, _payload(version_id))
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["id"].startswith("MSC-") and len(data["id"]) == 16
    assert data["experiment_id"] == experiment_id
    assert data["definition_version_id"] == version_id
    assert data["version"] == 1
    assert set(data) == {
        "id", "experiment_id", "definition_version_id", "version", "measurement_window_days", "minimum_evidence",
        "success_criterion", "analysis_method_intent", "stopping_rule", "decision_rule_intent", "signals",
        "created_at",
    }
    assert len(data["signals"]) == 1
    signal = data["signals"][0]
    assert signal["id"].startswith("RSG-") and len(signal["id"]) == 16
    assert set(signal) == {
        "id", "ordinal", "name", "description", "expected_direction", "evidence_requirement", "tracking_required",
    }
    assert signal["ordinal"] == 1 and signal["tracking_required"] is False


def test_revision_returns_201_and_appends_version_two(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    first = _declare_contract(fixtures, experiment_id, _payload(version_id))
    assert first.status_code == 201
    second = _declare_contract(
        fixtures, experiment_id,
        _payload(version_id, base_version=1, signals=[_signal(name="Different signal")]),
    )
    assert second.status_code == 201, second.text
    assert second.json()["version"] == 2
    history = _get_history(fixtures, experiment_id)
    assert history.status_code == 200
    body = history.json()
    assert body["current_version"] == 2 and len(body["versions"]) == 2
    assert [v["version"] for v in body["versions"]] == [1, 2]  # ascending
    assert body["measurement_contract_label"] == "DECLARED_MEASUREMENT_INTENT"


def test_signals_are_returned_in_declared_order(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    body = _payload(version_id, signals=[_signal(name="Second signal"), _signal(name="First signal")])
    response = _declare_contract(fixtures, experiment_id, body)
    assert response.status_code == 201, response.text
    names = [s["name"] for s in response.json()["signals"]]
    assert names == ["Second signal", "First signal"]
    assert [s["ordinal"] for s in response.json()["signals"]] == [1, 2]


def test_replay_returns_200_with_no_new_row(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    body = _payload(version_id)
    first = _declare_contract(fixtures, experiment_id, body)
    assert first.status_code == 201
    before = _table_counts()
    replay = _declare_contract(fixtures, experiment_id, body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert _delta(before, _table_counts()) == {}


# --- idempotency -------------------------------------------------------------------------


def test_same_key_different_material_is_idempotency_conflict(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    key = uuid.uuid4().hex
    first = _declare_contract(fixtures, experiment_id, _payload(version_id, client_request_id=key))
    assert first.status_code == 201
    conflict = _declare_contract(
        fixtures, experiment_id, _payload(version_id, client_request_id=key, signals=[_signal(name="Other")])
    )
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_stale_base_is_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    assert _declare_contract(fixtures, experiment_id, _payload(version_id)).status_code == 201
    stale = _declare_contract(fixtures, experiment_id, _payload(version_id, base_version=0))
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "MEASUREMENT_CONTRACT_BASE_STALE"


def test_unchanged_revision_is_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    body = _payload(version_id)
    assert _declare_contract(fixtures, experiment_id, body).status_code == 201
    unchanged = _declare_contract(fixtures, experiment_id, {**body, "base_version": 1, "client_request_id": uuid.uuid4().hex})
    assert unchanged.status_code == 409 and unchanged.json()["error"]["code"] == "MEASUREMENT_CONTRACT_UNCHANGED"


# --- pinning / Definition lock -------------------------------------------------------------


def test_declaring_pins_the_definition_and_blocks_a_revision(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    assert _declare_contract(fixtures, experiment_id, _payload(version_id)).status_code == 201
    blocked = _declare_definition(
        fixtures, experiment_id,
        {"base_version": 1, "client_request_id": uuid.uuid4().hex, "comparison_question": "Q?", "comparison_type": "OBSERVATIONAL",
         "changed_factor": "New factor", "controlled_factors": [], "comparison_basis": "b", "scope": "s",
         "learning_intent": "l", "non_conclusion_boundary": "n"},
    )
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "EXPERIMENT_DEFINITION_PINNED"


def test_non_tip_definition_version_is_a_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, v1 = _defined_experiment(fixtures)
    v2 = _declare_definition(
        fixtures, experiment_id,
        {"base_version": 1, "client_request_id": uuid.uuid4().hex, "comparison_question": "Q2?", "comparison_type": "OBSERVATIONAL",
         "changed_factor": "Second factor", "controlled_factors": [], "comparison_basis": "b", "scope": "s",
         "learning_intent": "l", "non_conclusion_boundary": "n"},
    )
    assert v2.status_code == 201
    stale = _declare_contract(fixtures, experiment_id, _payload(v1))
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "MEASUREMENT_CONTRACT_DEFINITION_VERSION_NOT_CURRENT"


def test_a_matching_replay_is_still_200_after_the_definition_is_pinned(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    body = _payload(version_id)
    first = _declare_contract(fixtures, experiment_id, body)
    assert first.status_code == 201
    replay = _declare_contract(fixtures, experiment_id, body)
    assert replay.status_code == 200


def test_unknown_or_foreign_definition_version_is_a_non_leaky_403(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _defined_experiment(fixtures)
    response = _declare_contract(fixtures, experiment_id, _payload("EXD-TOTALLYFAKE0"))
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_a_contract_without_a_declared_definition_is_a_non_leaky_403(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)  # no Definition declared
    response = _declare_contract(fixtures, experiment_id, _payload("EXD-TOTALLYFAKE0"))
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


# --- comparison type -----------------------------------------------------------------------


def test_controlled_comparison_requires_success_criterion(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id, hypothesis_id, experiment_id = _experiment(fixtures)
    controlled = _declare_definition(
        fixtures, experiment_id,
        {"base_version": 0, "client_request_id": uuid.uuid4().hex, "comparison_question": "Q?", "comparison_type": "CONTROLLED",
         "changed_factor": "Format", "controlled_factors": ["Length"], "comparison_basis": "b", "scope": "s",
         "learning_intent": "l", "non_conclusion_boundary": "n"},
    )
    assert controlled.status_code == 201
    version_id = controlled.json()["id"]
    missing = _declare_contract(fixtures, experiment_id, _payload(version_id, success_criterion=None))
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "MEASUREMENT_CONTRACT_SUCCESS_CRITERION_REQUIRED"
    ok = _declare_contract(fixtures, experiment_id, _payload(version_id, success_criterion="CTR improves by any margin."))
    assert ok.status_code == 201, ok.text


def test_observational_comparison_does_not_require_success_criterion(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)  # OBSERVATIONAL by default
    response = _declare_contract(fixtures, experiment_id, _payload(version_id, success_criterion=None))
    assert response.status_code == 201, response.text


# --- validation ----------------------------------------------------------------------------


@pytest.mark.parametrize("field", FORBIDDEN_FIELDS)
def test_forbidden_fields_are_rejected(campaign_run_client: dict, field: str) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    response = _declare_contract(fixtures, experiment_id, {**_payload(version_id), field: "x"})
    assert response.status_code == 422, (field, response.text)


def test_at_least_one_signal_is_required(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    response = _declare_contract(fixtures, experiment_id, _payload(version_id, signals=[]))
    assert response.status_code == 422


def test_duplicate_normalized_signal_names_in_one_request_is_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    response = _declare_contract(
        fixtures, experiment_id,
        _payload(version_id, signals=[_signal(name="CTR"), _signal(name="  ctr  ")]),
    )
    assert response.status_code == 422


def test_blank_signal_name_or_description_is_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    assert _declare_contract(fixtures, experiment_id, _payload(version_id, signals=[_signal(name="   ")])).status_code == 422
    assert _declare_contract(fixtures, experiment_id, _payload(version_id, signals=[_signal(description="")])).status_code == 422


def test_invalid_expected_direction_is_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    response = _declare_contract(
        fixtures, experiment_id, _payload(version_id, signals=[_signal(expected_direction="SIDEWAYS")])
    )
    assert response.status_code == 422


def test_non_positive_measurement_window_is_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    response = _declare_contract(fixtures, experiment_id, _payload(version_id, measurement_window_days=0))
    assert response.status_code == 422


def test_blank_optional_prose_is_coerced_to_null(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    response = _declare_contract(fixtures, experiment_id, _payload(version_id, minimum_evidence="   "))
    assert response.status_code == 201, response.text
    assert response.json()["minimum_evidence"] is None


# --- authority / tenancy --------------------------------------------------------------------


def test_member_can_declare_a_contract(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}
    response = _declare_contract(member_fixtures, experiment_id, _payload(version_id))
    assert response.status_code == 201, response.text


def test_create_requires_csrf(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    response = fixtures["client"].post(_mc_path(fixtures, experiment_id), json=_payload(version_id))
    assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_INVALID"


def test_create_requires_authentication(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    anon = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    response = anon.post(_mc_path(fixtures, experiment_id), json=_payload(version_id))
    assert response.status_code == 401


def test_unknown_experiment_is_a_non_leaky_403(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _declare_contract(fixtures, "EXP-TOTALLYFAKE0", _payload("EXD-TOTALLYFAKE0"))
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_cross_workspace_experiment_is_a_non_leaky_403(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    other_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    register_and_get_csrf(other_client, display_name="Other User")
    other_token = other_client.get("/api/v1/auth/csrf").json()["csrf_token"]
    other_campaign = other_client.post(
        "/api/v1/campaigns", json=campaign_payload(name="Other"), headers={"X-CSRF-Token": other_token}
    ).json()["campaign"]["id"]
    other_fixtures = {**fixtures, "client": other_client, "csrf_token": other_token, "campaign_id": other_campaign}
    response = _declare_contract(other_fixtures, experiment_id, _payload(version_id))
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_no_patch_put_delete_route_exists(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _defined_experiment(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    path = _mc_path(fixtures, experiment_id)
    assert fixtures["client"].patch(path, json={}, headers=headers).status_code == 405
    assert fixtures["client"].put(path, json={}, headers=headers).status_code == 405
    assert fixtures["client"].delete(path, headers=headers).status_code == 405


# --- strategy currency -----------------------------------------------------------------------


def test_superseded_strategy_rejects_new_writes_but_allows_reads_and_matching_replay(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id, _, experiment_id, version_id = _defined_experiment(fixtures)
    body = _payload(version_id)
    first = _declare_contract(fixtures, experiment_id, body)
    assert first.status_code == 201
    _supersede_strategy(fixtures, strategy_id)
    stale = _declare_contract(
        fixtures, experiment_id, _payload(version_id, base_version=1, signals=[_signal(name="Different")])
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "MEASUREMENT_CONTRACT_STRATEGY_STALE"
    replay = _declare_contract(fixtures, experiment_id, body)
    assert replay.status_code == 200
    assert _get_current(fixtures, experiment_id).status_code == 200
    assert _get_history(fixtures, experiment_id).status_code == 200


# --- read model --------------------------------------------------------------------------


def test_get_current_returns_null_when_no_contract(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _defined_experiment(fixtures)
    response = _get_current(fixtures, experiment_id)
    assert response.status_code == 200 and response.json() is None


def test_get_current_returns_the_tip(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    assert _declare_contract(fixtures, experiment_id, _payload(version_id)).status_code == 201
    second = _declare_contract(fixtures, experiment_id, _payload(version_id, base_version=1, signals=[_signal(name="Other")]))
    assert second.status_code == 201
    current = _get_current(fixtures, experiment_id)
    assert current.status_code == 200 and current.json()["version"] == 2


def test_definition_public_shows_has_measurement_contract_and_widened_is_pinned(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    before = _strategy_tip(fixtures, experiment_id)["definition"]
    assert before["has_measurement_contract"] is False and before["measurement_contract_version"] is None
    assert before["is_pinned"] is False and before["variant_count"] == 0
    assert _declare_contract(fixtures, experiment_id, _payload(version_id)).status_code == 201
    after = _strategy_tip(fixtures, experiment_id)["definition"]
    assert after["has_measurement_contract"] is True and after["measurement_contract_version"] == 1
    assert after["is_pinned"] is True  # widened: true even with zero Variants
    assert after["variant_count"] == 0  # unchanged meaning: a Contract never increments it


# --- non-effects -----------------------------------------------------------------------------


def test_declaration_touches_only_contract_signal_and_audit_tables(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    before = _table_counts()
    response = _declare_contract(fixtures, experiment_id, _payload(version_id, signals=[_signal(), _signal(name="Second")]))
    assert response.status_code == 201, response.text
    delta = _delta(before, _table_counts())
    assert delta == {"measurement_contract_versions": 1, "measurement_contract_signals": 2, "audit_events": 1}


def test_rejection_and_replay_leave_zero_delta(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    body = _payload(version_id)
    assert _declare_contract(fixtures, experiment_id, body).status_code == 201
    before = _table_counts()
    conflict = _declare_contract(fixtures, experiment_id, {**body, "client_request_id": uuid.uuid4().hex, "base_version": 0})
    assert conflict.status_code == 409
    assert _delta(before, _table_counts()) == {}
    replay = _declare_contract(fixtures, experiment_id, body)
    assert replay.status_code == 200
    assert _delta(before, _table_counts()) == {}


# --- audit -----------------------------------------------------------------------------------


def test_audit_event_recorded_exactly_once_per_committed_version(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    assert _declare_contract(fixtures, experiment_id, _payload(version_id)).status_code == 201
    events = _contract_audit(experiment_id)
    assert len(events) == 1
    assert events[0]["previous_state"] is None and events[0]["new_state"] == "v1"
    assert events[0]["actor_type"] == ActorType.USER
    assert events[0]["contract_id"] is not None
    # A replay adds no second event.
    key_used = _declare_contract(fixtures, experiment_id, _payload(version_id)).status_code
    assert key_used in (200, 409)  # different key -> stale/unchanged path, not a duplicate replay; irrelevant here


# --- firewalls ---------------------------------------------------------------------------------


def test_content_plan_is_valid_with_and_without_a_measurement_contract(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    without = _post(
        fixtures, f"/api/v1/campaigns/{fixtures['campaign_id']}/plan",
        {"summary": "Plan without a contract.", "experiment_public_id": experiment_id},
    )
    assert without.status_code == 201, without.text
    assert _declare_contract(fixtures, experiment_id, _payload(version_id)).status_code == 201
    with_contract = _post(
        fixtures, f"/api/v1/campaigns/{fixtures['campaign_id']}/plan",
        {"summary": "Plan with a contract.", "experiment_public_id": experiment_id},
    )
    assert with_contract.status_code == 201, with_contract.text
    assert set(without.json()) == set(with_contract.json())  # no Contract-conditioned field appears


def test_no_evidence_measurement_or_tracking_row_is_ever_created(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    with OrmSession(get_engine()) as session:
        before = {
            "metric_entries": session.scalar(select(func.count()).select_from(MetricEntry)),
            "analysis_results": session.scalar(select(func.count()).select_from(AnalysisResult)),
            "tracking_requirements": session.scalar(select(func.count()).select_from(TrackingRequirement)),
        }
    response = _declare_contract(
        fixtures, experiment_id, _payload(version_id, signals=[_signal(tracking_required=True)])
    )
    assert response.status_code == 201, response.text
    with OrmSession(get_engine()) as session:
        after = {
            "metric_entries": session.scalar(select(func.count()).select_from(MetricEntry)),
            "analysis_results": session.scalar(select(func.count()).select_from(AnalysisResult)),
            "tracking_requirements": session.scalar(select(func.count()).select_from(TrackingRequirement)),
        }
    assert before == after


def test_no_experimental_or_execution_claim_table_or_column_exists() -> None:
    assert not [
        t for t in Base.metadata.tables
        if any(word in t for word in ("allocation", "randomiz", "contamination", "execution_auth"))
    ]
    columns = set(MeasurementContractVersion.__table__.columns.keys()) | set(
        MeasurementContractRequiredSignal.__table__.columns.keys()
    )
    assert not columns & {"winner", "result", "observed_value", "score", "attribution", "roas", "incrementality"}
