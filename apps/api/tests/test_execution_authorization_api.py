"""API contract, idempotency, prerequisite/cardinality, auto-supersession,
revocation, Contract-freeze, Strategy-currency, authority/tenancy, audit,
read-model, non-effects and firewall tests for Governed Execution
Authorization (MVP-40, implementing the frozen Design Freeze). All marked
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
from app.strategy.models import Experiment
from app.tracking.models import TrackingRequirement
from app.workspaces.models import MembershipRole
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.settingstest import add_member_to_workspace, login_as
from tests.test_experiment_api import _post
from tests.test_experiment_definition_api import (
    _declare as _declare_definition,
    _delta,
    _experiment,
    _payload as _definition_payload,
    _supersede_strategy,
    _table_counts,
)
from tests.test_experiment_variant_api import _declare_variant, _vpayload
from tests.test_measurement_contract_api import _declare_contract, _payload as _contract_payload, _signal

pytestmark = pytest.mark.postgres

FORBIDDEN_FIELDS = [
    "definition_version_id", "contract_version_id", "variant_ids", "variants", "experiment_id", "workspace_id",
    "status", "execution_started", "assignment", "allocation", "exposure", "tracking_valid", "measurement_ready",
    "winner", "result", "validity", "causality", "target", "revoked_at", "superseded_by",
]


def _ea_path(fixtures: dict, experiment_id: str, suffix: str = "", campaign_id: str | None = None) -> str:
    return (
        f"/api/v1/campaigns/{campaign_id or fixtures['campaign_id']}/experiments/{experiment_id}"
        f"/execution-authorization{suffix}"
    )


def _body(**overrides: object) -> dict:
    body: dict = {
        "client_request_id": uuid.uuid4().hex,
        "unit_of_assignment": "Website visitor (session)",
        "allocation_design": "Intended 50/50 split by session; declared intent only.",
    }
    body.update(overrides)
    return body


def _authorize(fixtures: dict, experiment_id: str, body: dict | None = None):
    return _post(fixtures, _ea_path(fixtures, experiment_id), body if body is not None else _body())


def _current(fixtures: dict, experiment_id: str):
    return fixtures["client"].get(_ea_path(fixtures, experiment_id))


def _history(fixtures: dict, experiment_id: str):
    return fixtures["client"].get(_ea_path(fixtures, experiment_id, "/history"))


def _revoke(fixtures: dict, experiment_id: str, reason: str | None = "Configuration was wrong."):
    body = {} if reason is None else {"reason": reason}
    return _post(fixtures, _ea_path(fixtures, experiment_id, "/revoke"), body)


def _ready(fixtures: dict, *, controlled: bool = False, variants: int = 1) -> tuple[str, str, str, str]:
    """(strategy, hypothesis, experiment, EXD id) with Definition + N Variants + a Contract."""
    strategy_id, hypothesis_id, experiment_id = _experiment(fixtures)
    definition_body = (
        _definition_payload(comparison_type="CONTROLLED", controlled_factors=["Format"]) if controlled
        else _definition_payload()
    )
    version = _declare_definition(fixtures, experiment_id, definition_body)
    assert version.status_code == 201, version.text
    version_id = version.json()["id"]
    for index in range(variants):
        response = _declare_variant(
            fixtures, experiment_id, _vpayload(version_id, label=f"Condition {index + 1}")
        )
        assert response.status_code == 201, response.text
    contract = _declare_contract(
        fixtures, experiment_id,
        _contract_payload(version_id, success_criterion="CTR improves." if controlled else None),
    )
    assert contract.status_code == 201, contract.text
    return strategy_id, hypothesis_id, experiment_id, version_id


def _authorization_audit(experiment_public_id: str) -> list[dict]:
    with OrmSession(get_engine()) as session:
        experiment = session.execute(select(Experiment).where(Experiment.public_id == experiment_public_id)).scalar_one()
        rows = session.execute(
            select(AuditEvent).where(
                AuditEvent.experiment_id == experiment.id,
                AuditEvent.event_type.like("strategy.execution_authorization.%"),
            ).order_by(AuditEvent.created_at, AuditEvent.id)
        ).scalars().all()
        return [
            {
                "event_type": r.event_type, "previous_state": r.previous_state, "new_state": r.new_state,
                "actor_type": r.actor_type, "authorization_id": r.execution_authorization_id,
            }
            for r in rows
        ]


# --- creation / shape ------------------------------------------------------------------------


def test_creation_returns_201_and_the_frozen_public_shape(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _ready(fixtures)
    response = _authorize(fixtures, experiment_id)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["id"].startswith("EXA-") and len(data["id"]) == 16
    assert data["experiment_id"] == experiment_id
    assert data["definition_version_id"] == version_id
    assert data["contract_version_id"].startswith("MSC-")
    assert set(data) == {
        "id", "experiment_id", "definition_version_id", "contract_version_id", "unit_of_assignment",
        "allocation_design", "variants", "signal_count", "tracking_required_signal_count", "active",
        "revoked_at", "revoked_reason", "superseded_by", "created_at",
    }
    assert data["active"] is True and data["revoked_at"] is None and data["superseded_by"] is None
    assert len(data["variants"]) == 1 and set(data["variants"][0]) == {"id", "label", "condition_description"}
    assert data["signal_count"] == 1 and data["tracking_required_signal_count"] == 0


def test_replay_returns_200_and_no_second_row_or_audit_event(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    body = _body()
    first = _authorize(fixtures, experiment_id, body)
    assert first.status_code == 201
    before = _table_counts()
    replay = _authorize(fixtures, experiment_id, body)
    assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"]
    assert _delta(before, _table_counts()) == {}
    assert len(_authorization_audit(experiment_id)) == 1


def test_same_key_different_material_is_idempotency_conflict(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    body = _body()
    assert _authorize(fixtures, experiment_id, body).status_code == 201
    conflict = _authorize(fixtures, experiment_id, {**body, "allocation_design": "A different design."})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_replay_is_whitespace_normalized(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    body = _body()
    assert _authorize(fixtures, experiment_id, body).status_code == 201
    replay = _authorize(fixtures, experiment_id, {**body, "unit_of_assignment": f"  {body['unit_of_assignment']}  "})
    assert replay.status_code == 200


def test_a_revoked_authorizations_key_is_not_released(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    body = _body()
    assert _authorize(fixtures, experiment_id, body).status_code == 201
    assert _revoke(fixtures, experiment_id).status_code == 200
    # Same key + same material after revocation: material still matches the old row -> replay of the
    # historical (revoked) row, never a silent re-activation.
    again = _authorize(fixtures, experiment_id, body)
    assert again.status_code in (200, 409)
    current = _current(fixtures, experiment_id)
    assert current.status_code == 200 and current.json() is None


# --- prerequisites / cardinality -------------------------------------------------------------


def test_authorization_requires_a_measurement_contract(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    version = _declare_definition(fixtures, experiment_id)
    assert _declare_variant(fixtures, experiment_id, _vpayload(version.json()["id"])).status_code == 201
    response = _authorize(fixtures, experiment_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EXECUTION_AUTHORIZATION_NO_MEASUREMENT_CONTRACT"


def test_observational_needs_one_variant(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    version = _declare_definition(fixtures, experiment_id)
    assert _declare_contract(fixtures, experiment_id, _contract_payload(version.json()["id"])).status_code == 201
    response = _authorize(fixtures, experiment_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EXECUTION_AUTHORIZATION_INSUFFICIENT_VARIANTS"


def test_controlled_needs_two_variants(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _ready(fixtures, controlled=True, variants=1)
    response = _authorize(fixtures, experiment_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EXECUTION_AUTHORIZATION_INSUFFICIENT_VARIANTS"
    assert _declare_variant(fixtures, experiment_id, _vpayload(version_id, label="Second")).status_code == 201
    ok = _authorize(fixtures, experiment_id)
    assert ok.status_code == 201 and len(ok.json()["variants"]) == 2


def test_snapshot_is_complete_and_excludes_variants_declared_later(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _ready(fixtures, variants=2)
    first = _authorize(fixtures, experiment_id)
    assert first.status_code == 201 and len(first.json()["variants"]) == 2
    # Variant declaration remains legal after Authorization.
    late = _declare_variant(fixtures, experiment_id, _vpayload(version_id, label="Late"))
    assert late.status_code == 201, late.text
    current = _current(fixtures, experiment_id).json()
    assert len(current["variants"]) == 2  # historical snapshot unchanged
    assert late.json()["id"] not in {v["id"] for v in current["variants"]}
    # A fresh Authorization includes it.
    second = _authorize(fixtures, experiment_id)
    assert second.status_code == 201 and len(second.json()["variants"]) == 3


def test_tracking_required_is_informational_only_and_never_blocks(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    version = _declare_definition(fixtures, experiment_id)
    version_id = version.json()["id"]
    assert _declare_variant(fixtures, experiment_id, _vpayload(version_id)).status_code == 201
    contract = _declare_contract(
        fixtures, experiment_id,
        _contract_payload(version_id, signals=[_signal(tracking_required=True), _signal(name="Second")]),
    )
    assert contract.status_code == 201
    with OrmSession(get_engine()) as session:
        before = session.scalar(select(func.count()).select_from(TrackingRequirement))
    response = _authorize(fixtures, experiment_id)
    assert response.status_code == 201, response.text
    assert response.json()["signal_count"] == 2 and response.json()["tracking_required_signal_count"] == 1
    with OrmSession(get_engine()) as session:
        assert session.scalar(select(func.count()).select_from(TrackingRequirement)) == before


# --- validation ------------------------------------------------------------------------------


@pytest.mark.parametrize("field", FORBIDDEN_FIELDS)
def test_forbidden_fields_are_rejected(campaign_run_client: dict, field: str) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    response = _authorize(fixtures, experiment_id, {**_body(), field: "x"})
    assert response.status_code == 422, (field, response.text)


def test_blank_or_missing_configuration_is_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    assert _authorize(fixtures, experiment_id, _body(unit_of_assignment="   ")).status_code == 422
    assert _authorize(fixtures, experiment_id, _body(allocation_design="")).status_code == 422
    missing = _body()
    del missing["allocation_design"]
    assert _authorize(fixtures, experiment_id, missing).status_code == 422


def test_unit_must_be_single_line_and_nul_is_rejected(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    assert _authorize(fixtures, experiment_id, _body(unit_of_assignment="a\nb")).status_code == 422
    assert _authorize(fixtures, experiment_id, _body(allocation_design="a\x00b")).status_code == 422


def test_case_is_preserved(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    response = _authorize(fixtures, experiment_id, _body(unit_of_assignment="MiXeD Case"))
    assert response.status_code == 201 and response.json()["unit_of_assignment"] == "MiXeD Case"


# --- authority / tenancy ---------------------------------------------------------------------


def test_member_can_authorize_and_revoke(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}
    assert _authorize(member_fixtures, experiment_id).status_code == 201
    assert _revoke(member_fixtures, experiment_id).status_code == 200


def test_authorize_and_revoke_require_csrf_and_authentication(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    for suffix in ("", "/revoke"):
        no_csrf = fixtures["client"].post(_ea_path(fixtures, experiment_id, suffix), json=_body())
        assert no_csrf.status_code == 403 and no_csrf.json()["error"]["code"] == "CSRF_INVALID"
        anon = TestClient(fixtures["client"].app, raise_server_exceptions=False)
        assert anon.post(_ea_path(fixtures, experiment_id, suffix), json=_body()).status_code == 401


def test_unknown_experiment_is_a_non_leaky_403(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _authorize(fixtures, "EXP-TOTALLYFAKE0")
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"
    assert fixtures["client"].get(_ea_path(fixtures, "EXP-TOTALLYFAKE0")).status_code == 403
    assert _revoke(fixtures, "EXP-TOTALLYFAKE0").status_code == 403


def test_cross_workspace_experiment_is_a_non_leaky_403(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    other_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    register_and_get_csrf(other_client, display_name="Other User")
    other_token = other_client.get("/api/v1/auth/csrf").json()["csrf_token"]
    other_campaign = other_client.post(
        "/api/v1/campaigns", json=campaign_payload(name="Other"), headers={"X-CSRF-Token": other_token}
    ).json()["campaign"]["id"]
    other = {**fixtures, "client": other_client, "csrf_token": other_token, "campaign_id": other_campaign}
    assert _authorize(other, experiment_id).status_code == 403
    assert other["client"].get(_ea_path(other, experiment_id)).status_code == 403
    assert other["client"].get(_ea_path(other, experiment_id, "/history")).status_code == 403
    assert _revoke(other, experiment_id).status_code == 403


def test_no_patch_put_delete_route_exists(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    for suffix in ("", "/revoke"):
        path = _ea_path(fixtures, experiment_id, suffix)
        assert fixtures["client"].patch(path, json={}, headers=headers).status_code == 405
        assert fixtures["client"].put(path, json={}, headers=headers).status_code == 405
        assert fixtures["client"].delete(path, headers=headers).status_code == 405


# --- auto-supersession / revocation ---------------------------------------------------------


def test_reauthorization_atomically_supersedes_the_active_one(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    first = _authorize(fixtures, experiment_id)
    second = _authorize(fixtures, experiment_id, _body(allocation_design="Sequential rotation by visit."))
    assert first.status_code == 201 and second.status_code == 201
    history = _history(fixtures, experiment_id).json()
    assert history["current_id"] == second.json()["id"]
    assert [a["id"] for a in history["authorizations"]] == [first.json()["id"], second.json()["id"]]
    old, new = history["authorizations"]
    assert old["active"] is False and old["revoked_reason"] == "superseded by re-authorization"
    assert old["superseded_by"] == new["id"] and old["revoked_at"] is not None
    assert new["active"] is True and new["superseded_by"] is None
    assert _current(fixtures, experiment_id).json()["id"] == new["id"]
    active_count = sum(1 for a in history["authorizations"] if a["active"])
    assert active_count == 1


def test_supersession_never_links_across_experiments(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, hypothesis_id, first_experiment, _v = _ready(fixtures)
    second = _post(
        fixtures, f"/api/v1/campaigns/{fixtures['campaign_id']}/hypotheses/{hypothesis_id}/experiments",
        {"description": "A second experiment."},
    )
    assert second.status_code == 201, second.text
    second_experiment = second.json()["id"]
    second_version = _declare_definition(fixtures, second_experiment).json()["id"]
    assert _declare_variant(fixtures, second_experiment, _vpayload(second_version)).status_code == 201
    assert _declare_contract(fixtures, second_experiment, _contract_payload(second_version)).status_code == 201
    a = _authorize(fixtures, first_experiment)
    b = _authorize(fixtures, second_experiment)
    assert a.status_code == 201 and b.status_code == 201
    assert _history(fixtures, first_experiment).json()["current_id"] == a.json()["id"]
    assert _history(fixtures, second_experiment).json()["current_id"] == b.json()["id"]
    with OrmSession(get_engine()) as session:
        from app.strategy.models import ExecutionAuthorization

        rows = session.execute(
            select(ExecutionAuthorization).join(Experiment, ExecutionAuthorization.experiment_id == Experiment.id).where(
                Experiment.public_id.in_([first_experiment, second_experiment])
            )
        ).scalars().all()
        assert len(rows) == 2
        assert all(row.superseded_by_execution_authorization_id is None for row in rows)


def test_manual_revocation_requires_reason_and_an_active_authorization(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    none_active = _revoke(fixtures, experiment_id)
    assert none_active.status_code == 409
    assert none_active.json()["error"]["code"] == "EXECUTION_AUTHORIZATION_NONE_ACTIVE"
    created = _authorize(fixtures, experiment_id)
    assert _revoke(fixtures, experiment_id, reason=None).status_code == 422
    assert _revoke(fixtures, experiment_id, reason="   ").status_code == 422
    revoked = _revoke(fixtures, experiment_id, reason="Wrong unit of assignment.")
    assert revoked.status_code == 200, revoked.text
    data = revoked.json()
    assert data["id"] == created.json()["id"] and data["active"] is False
    assert data["revoked_reason"] == "Wrong unit of assignment." and data["superseded_by"] is None
    assert _current(fixtures, experiment_id).json() is None
    again = _revoke(fixtures, experiment_id)
    assert again.status_code == 409 and again.json()["error"]["code"] == "EXECUTION_AUTHORIZATION_NONE_ACTIVE"
    # Revocation is one-way: history keeps the row, nothing is deleted or reactivated.
    assert len(_history(fixtures, experiment_id).json()["authorizations"]) == 1


def test_a_new_authorization_after_manual_revocation_is_a_fresh_row(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    first = _authorize(fixtures, experiment_id)
    assert _revoke(fixtures, experiment_id).status_code == 200
    second = _authorize(fixtures, experiment_id)
    assert second.status_code == 201 and second.json()["id"] != first.json()["id"]
    history = _history(fixtures, experiment_id).json()["authorizations"]
    assert history[0]["superseded_by"] is None  # manual revocation never sets a successor
    assert history[1]["active"] is True


# --- Contract freeze / Definition integrity --------------------------------------------------


def test_contract_revision_is_frozen_while_active_and_reopens_after_revocation(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _ready(fixtures)
    assert _authorize(fixtures, experiment_id).status_code == 201
    frozen = _declare_contract(
        fixtures, experiment_id,
        _contract_payload(version_id, base_version=1, signals=[_signal(name="Different")]),
    )
    assert frozen.status_code == 409
    assert frozen.json()["error"]["code"] == "MEASUREMENT_CONTRACT_FROZEN_BY_AUTHORIZATION"
    assert _revoke(fixtures, experiment_id).status_code == 200
    reopened = _declare_contract(
        fixtures, experiment_id,
        _contract_payload(version_id, base_version=1, signals=[_signal(name="Different")]),
    )
    assert reopened.status_code == 201, reopened.text
    assert reopened.json()["version"] == 2


def test_reauthorization_pins_the_new_contract_tip_after_a_revision(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _ready(fixtures)
    first = _authorize(fixtures, experiment_id)
    assert _revoke(fixtures, experiment_id).status_code == 200
    assert _declare_contract(
        fixtures, experiment_id, _contract_payload(version_id, base_version=1, signals=[_signal(name="Other")])
    ).status_code == 201
    second = _authorize(fixtures, experiment_id)
    assert second.status_code == 201
    assert second.json()["contract_version_id"] != first.json()["contract_version_id"]


def test_definition_revision_is_structurally_blocked_by_existing_pinning(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    blocked = _declare_definition(fixtures, experiment_id, _definition_payload(base_version=1, changed_factor="Blocked"))
    assert blocked.status_code == 409
    assert _authorize(fixtures, experiment_id).status_code == 201
    still = _declare_definition(fixtures, experiment_id, _definition_payload(base_version=1, changed_factor="Blocked2"))
    assert still.status_code == 409


# --- strategy currency -----------------------------------------------------------------------


def test_superseded_strategy_rejects_new_authorizations_but_allows_reads_and_matching_replay(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id, _, experiment_id, _v = _ready(fixtures)
    body = _body()
    assert _authorize(fixtures, experiment_id, body).status_code == 201
    _supersede_strategy(fixtures, strategy_id)
    stale = _authorize(fixtures, experiment_id, _body())
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "EXECUTION_AUTHORIZATION_STRATEGY_STALE"
    assert _authorize(fixtures, experiment_id, body).status_code == 200  # matching replay still succeeds
    current = _current(fixtures, experiment_id)
    assert current.status_code == 200 and current.json()["active"] is True  # not auto-invalidated
    assert _history(fixtures, experiment_id).status_code == 200


# --- read model ------------------------------------------------------------------------------


def test_get_current_is_null_before_any_authorization(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    response = _current(fixtures, experiment_id)
    assert response.status_code == 200 and response.json() is None
    history = _history(fixtures, experiment_id).json()
    assert history["current_id"] is None and history["authorizations"] == []


def test_read_model_exposes_no_validity_or_execution_labels(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    _authorize(fixtures, experiment_id)
    text = (_current(fixtures, experiment_id).text + _history(fixtures, experiment_id).text).lower()
    for word in ("valid", "causal", "measurement_ready", "tracking_ready", "execution_started", "winner", "result"):
        assert word not in text, word


# --- audit -----------------------------------------------------------------------------------


def test_audit_events_for_authorize_supersede_and_revoke(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    first = _authorize(fixtures, experiment_id)
    events = _authorization_audit(experiment_id)
    assert len(events) == 1
    assert events[0]["event_type"] == "strategy.execution_authorization.authorized"
    assert events[0]["previous_state"] is None and events[0]["new_state"] == "ACTIVE"
    assert events[0]["actor_type"] == ActorType.USER and events[0]["authorization_id"] is not None
    second = _authorize(fixtures, experiment_id, _body(allocation_design="Other design."))
    events = _authorization_audit(experiment_id)
    assert [e["event_type"] for e in events].count("strategy.execution_authorization.authorized") == 2
    revoked = [e for e in events if e["event_type"] == "strategy.execution_authorization.revoked"]
    assert len(revoked) == 1 and revoked[0]["new_state"] == f"superseded_by:{second.json()['id']}"
    assert _revoke(fixtures, experiment_id).status_code == 200
    events = _authorization_audit(experiment_id)
    manual = [e for e in events if e["event_type"] == "strategy.execution_authorization.revoked"]
    assert len(manual) == 2 and manual[-1]["new_state"] == "REVOKED"
    assert first.status_code == 201


def test_failed_attempts_write_no_audit_event(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    assert _authorize(fixtures, experiment_id).status_code == 409  # no contract
    assert _revoke(fixtures, experiment_id).status_code == 409
    assert _authorize(fixtures, experiment_id, {**_body(), "status": "x"}).status_code == 422
    assert _authorization_audit(experiment_id) == []


# --- non-effects / firewalls -----------------------------------------------------------------


def test_authorization_touches_only_its_own_tables_and_audit(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures, variants=2)
    before = _table_counts()
    assert _authorize(fixtures, experiment_id).status_code == 201
    assert _delta(before, _table_counts()) == {
        "execution_authorizations": 1, "execution_authorization_variants": 2, "audit_events": 1,
    }
    before = _table_counts()
    assert _authorize(fixtures, experiment_id).status_code == 201  # supersession: +1 row, 2 snapshot, 2 events
    assert _delta(before, _table_counts()) == {
        "execution_authorizations": 1, "execution_authorization_variants": 2, "audit_events": 2,
    }
    before = _table_counts()
    assert _revoke(fixtures, experiment_id).status_code == 200
    assert _delta(before, _table_counts()) == {"audit_events": 1}


def test_rejections_leave_zero_delta(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)
    body = _body()
    assert _authorize(fixtures, experiment_id, body).status_code == 201
    before = _table_counts()
    assert _authorize(fixtures, experiment_id, {**body, "allocation_design": "changed"}).status_code == 409
    assert _authorize(fixtures, experiment_id, body).status_code == 200
    assert _authorize(fixtures, experiment_id, {**_body(), "status": "x"}).status_code == 422
    assert _delta(before, _table_counts()) == {}


def test_no_evidence_metric_analysis_or_tracking_row_is_created(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _v = _ready(fixtures)

    def counts() -> dict:
        with OrmSession(get_engine()) as session:
            return {
                model.__name__: session.scalar(select(func.count()).select_from(model))
                for model in (MetricEntry, AnalysisResult, TrackingRequirement)
            }

    before = counts()
    assert _authorize(fixtures, experiment_id).status_code == 201
    assert counts() == before


def test_no_downstream_execution_claim_table_or_route_exists() -> None:
    from app.main import create_app

    assert not [
        t for t in Base.metadata.tables
        if any(word in t for word in ("allocation", "assignment", "randomiz", "contamination", "exposure"))
    ]
    paths = create_app().openapi()["paths"]
    for word in ("allocation", "assignment", "exposure", "winner", "attribution", "start-execution"):
        assert not [p for p in paths if word in p], word


def test_response_schema_has_no_forbidden_field() -> None:
    from app.strategy.schemas import ExecutionAuthorizationPublic

    fields = set(ExecutionAuthorizationPublic.model_fields)
    assert not fields & {
        "status", "execution_started", "assignment_started", "exposure_started", "tracking_valid",
        "measurement_ready", "result", "winner", "loser", "validity", "causality",
    }
