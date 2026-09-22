"""API tests for the Pre-Execution Measurement Declaration: it extends the EXISTING
Measurement Contract write / read / history surfaces (no new route family), is
all-or-nothing, maps its typed rejections through the existing error architecture,
inherits the C1 freeze, constrains Evidence Claims of STRUCTURED Contracts only,
adds no audit event type, and never leaks another workspace. All marked `postgres`.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.test_evidence_claim_api import _claim, _body as _claim_body, _metric, _signals
from tests.test_execution_authorization_api import _authorize, _experiment
from tests.test_execution_start_api import _start
from tests.test_experiment_definition_api import _declare as _declare_definition, _payload as _definition_payload
from tests.test_experiment_variant_api import _declare_variant, _vpayload
from tests.test_measurement_contract_api import (
    _contract_audit,
    _declare_contract,
    _defined_experiment,
    _get_current,
    _get_history,
    _payload,
    _signal,
)

pytestmark = pytest.mark.postgres

FORBIDDEN_WORDS = ("eligible", "validated", "successful", "pre_registration", "preregistration", "winner", "result")


def _bound(name: str = "Click-through rate", metric: str = "clicks", binding: str = "ANY", channel=None, min_points=1):
    return _signal(
        name=name, bound_metric_name=metric, channel_binding=binding, bound_channel=channel, min_data_points=min_points
    )


def _structured(version_id: str, level: str = "DESCRIPTIVE", **overrides: object) -> dict:
    body = _payload(
        version_id,
        declaration_level=level,
        declaration_semantics_version=1,
        measurement_window_days=14,
        baseline_window_days=7 if level == "COMPARATIVE" else None,
        signals=[_bound(min_points=None if level == "COMPARATIVE" else 1)],
    )
    body.update(overrides)
    return body


def _code(response) -> str:
    return response.json()["error"]["code"]


# --- legacy compatibility ----------------------------------------------------------------------------------------


def test_a_legacy_request_without_any_new_field_still_works_and_reads_back_null(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    response = _declare_contract(fixtures, experiment_id, _payload(version_id))
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["declaration_level"] is None and data["declaration_semantics_version"] is None
    assert data["baseline_window_days"] is None
    assert all(s["bound_metric_name"] is None and s["channel_binding"] is None for s in data["signals"])


# --- structured creation / readback ----------------------------------------------------------------------------------


def test_structured_descriptive_is_created_replayed_and_read_back(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    body = _structured(version_id)
    created = _declare_contract(fixtures, experiment_id, body)
    assert created.status_code == 201, created.text
    data = created.json()
    assert (data["declaration_level"], data["declaration_semantics_version"], data["baseline_window_days"]) == (
        "DESCRIPTIVE", 1, None,
    )
    signal = data["signals"][0]
    assert (signal["bound_metric_name"], signal["channel_binding"], signal["bound_channel"], signal["min_data_points"]) == (
        "clicks", "ANY", None, 1,
    )
    replay = _declare_contract(fixtures, experiment_id, body)
    assert replay.status_code == 200 and replay.json()["id"] == data["id"]
    assert _get_current(fixtures, experiment_id).json()["declaration_level"] == "DESCRIPTIVE"
    assert _get_history(fixtures, experiment_id).json()["versions"][0]["signals"][0]["bound_metric_name"] == "clicks"


def test_structured_comparative_is_created_with_a_baseline_and_no_min_data_points(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    created = _declare_contract(fixtures, experiment_id, _structured(version_id, "COMPARATIVE"))
    assert created.status_code == 201, created.text
    data = created.json()
    assert (data["declaration_level"], data["baseline_window_days"]) == ("COMPARATIVE", 7)
    assert data["signals"][0]["min_data_points"] is None


def test_binding_strings_are_trimmed(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    body = _structured(version_id, signals=[_bound(metric="  clicks ", binding="EXACT", channel=" email  ")])
    created = _declare_contract(fixtures, experiment_id, body)
    assert created.status_code == 201, created.text
    signal = created.json()["signals"][0]
    assert (signal["bound_metric_name"], signal["bound_channel"]) == ("clicks", "email")


def test_no_response_property_claims_eligibility_validation_or_success(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    data = _declare_contract(fixtures, experiment_id, _structured(version_id)).json()
    keys = set(data) | {key for signal in data["signals"] for key in signal}
    assert not {key for key in keys if any(word in key for word in FORBIDDEN_WORDS)}


# --- all-or-nothing / validation -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutation",
    [
        {"declaration_semantics_version": None},  # level without version
        {"declaration_level": None},  # version without level
        {"declaration_semantics_version": 2},
        {"declaration_semantics_version": "1"},  # strict integer
        {"declaration_level": "CONTROLLED"},
        {"declaration_level": "descriptive"},
        {"measurement_window_days": None},
        {"measurement_window_days": 3},
        {"measurement_window_days": 3651},
        {"baseline_window_days": 7},  # DESCRIPTIVE with a baseline
        {"signals": [_bound(min_points=None)]},  # DESCRIPTIVE needs min_data_points
        {"signals": [_bound(min_points=0)]},
        {"signals": [_signal()]},  # unbound signal in a structured declaration
        {"signals": [_bound(binding="ANY", channel="email")]},
        {"signals": [_bound(binding="EXACT", channel=None)]},
        {"signals": [_bound(binding="SOME")]},
        {"signals": [_bound(metric="   ")]},
        {"signals": [_bound(metric="line\nbreak")]},
    ],
)
def test_partially_structured_or_malformed_requests_are_422(campaign_run_client: dict, mutation: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    response = _declare_contract(fixtures, experiment_id, _structured(version_id, **mutation))
    assert response.status_code == 422, (mutation, response.text)
    assert _get_current(fixtures, experiment_id).json() is None  # nothing was written


@pytest.mark.parametrize(
    "mutation",
    [
        {"baseline_window_days": None},
        {"baseline_window_days": 3},
        {"baseline_window_days": 3651},
        {"signals": [_bound(min_points=2)]},  # COMPARATIVE forbids min_data_points
    ],
)
def test_comparative_specific_rules_are_422(campaign_run_client: dict, mutation: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    body = _structured(version_id, "COMPARATIVE", **mutation)
    assert _declare_contract(fixtures, experiment_id, body).status_code == 422


def test_a_legacy_declaration_carrying_a_structured_field_is_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    for body in (
        _payload(version_id, baseline_window_days=7),
        _payload(version_id, declaration_semantics_version=1),
        _payload(version_id, signals=[_bound()]),
    ):
        assert _declare_contract(fixtures, experiment_id, body).status_code == 422, body


def test_legacy_windows_below_the_structured_minimum_stay_legal(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    assert _declare_contract(fixtures, experiment_id, _payload(version_id, measurement_window_days=1)).status_code == 201


# --- typed rejections -------------------------------------------------------------------------------------------------


def test_a_structured_declaration_on_a_controlled_definition_is_a_typed_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _strategy, _hypothesis, experiment_id = _experiment(fixtures)
    version = _declare_definition(
        fixtures, experiment_id, _definition_payload(comparison_type="CONTROLLED", controlled_factors=["Format"])
    )
    assert version.status_code == 201, version.text
    body = _structured(version.json()["id"], success_criterion="CTR improves.")
    rejected = _declare_contract(fixtures, experiment_id, body)
    assert rejected.status_code == 422 and _code(rejected) == "MEASUREMENT_CONTRACT_CONTROLLED_DECLARATION_NOT_SUPPORTED"
    legacy = _declare_contract(
        fixtures, experiment_id, _payload(version.json()["id"], success_criterion="CTR improves.")
    )
    assert legacy.status_code == 201


@pytest.mark.parametrize(
    "signals",
    [
        [_bound("A", binding="ANY"), _bound("B", binding="EXACT", channel="email")],
        [_bound("A", binding="EXACT", channel="email"), _bound("B", binding="ANY")],
        [_bound("A", binding="ANY"), _bound("B", binding="ANY")],
        [_bound("A", binding="EXACT", channel="email"), _bound("B", binding="EXACT", channel="email")],
    ],
)
def test_binding_conflicts_are_a_typed_422(campaign_run_client: dict, signals: list) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    response = _declare_contract(fixtures, experiment_id, _structured(version_id, signals=signals))
    assert response.status_code == 422 and _code(response) == "MEASUREMENT_CONTRACT_BINDING_CONFLICT"


def test_distinct_exact_channels_are_accepted(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    signals = [_bound("A", binding="EXACT", channel="email"), _bound("B", binding="EXACT", channel="sms")]
    assert _declare_contract(fixtures, experiment_id, _structured(version_id, signals=signals)).status_code == 201


def test_the_ownership_rule_holds_during_revision(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    assert _declare_contract(fixtures, experiment_id, _structured(version_id)).status_code == 201
    revision = _structured(
        version_id, base_version=1, signals=[_bound("A", binding="ANY"), _bound("B", binding="EXACT", channel="email")]
    )
    rejected = _declare_contract(fixtures, experiment_id, revision)
    assert rejected.status_code == 422 and _code(rejected) == "MEASUREMENT_CONTRACT_BINDING_CONFLICT"


# --- material equality / idempotency ---------------------------------------------------------------------------------


def test_same_key_changed_semantics_is_409_and_unchanged_new_key_is_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    body = _structured(version_id)
    assert _declare_contract(fixtures, experiment_id, body).status_code == 201
    changed = _declare_contract(fixtures, experiment_id, {**body, "baseline_window_days": None, "declaration_level": "DESCRIPTIVE", "measurement_window_days": 15})
    assert changed.status_code == 409 and _code(changed) == "IDEMPOTENCY_KEY_CONFLICT"
    rebound = _declare_contract(fixtures, experiment_id, {**body, "signals": [_bound(metric="reach")]})
    assert rebound.status_code == 409 and _code(rebound) == "IDEMPOTENCY_KEY_CONFLICT"
    unchanged = _declare_contract(
        fixtures, experiment_id, {**body, "client_request_id": uuid.uuid4().hex, "base_version": 1}
    )
    assert unchanged.status_code == 409 and _code(unchanged) == "MEASUREMENT_CONTRACT_UNCHANGED"


def test_changed_structured_semantics_with_a_new_key_appends_a_version_and_history_reconstructs_it(
    campaign_run_client: dict,
) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    assert _declare_contract(fixtures, experiment_id, _payload(version_id)).status_code == 201  # LEGACY v1
    second = _declare_contract(fixtures, experiment_id, _structured(version_id, base_version=1))
    assert second.status_code == 201, second.text  # LEGACY -> STRUCTURED is material
    third = _declare_contract(fixtures, experiment_id, _structured(version_id, "COMPARATIVE", base_version=2))
    assert third.status_code == 201, third.text
    versions = _get_history(fixtures, experiment_id).json()["versions"]
    assert [(v["version"], v["declaration_level"], v["baseline_window_days"]) for v in versions] == [
        (1, None, None), (2, "DESCRIPTIVE", None), (3, "COMPARATIVE", 7),
    ]


# --- C1 freeze inheritance ------------------------------------------------------------------------------------------


def test_active_authorization_and_any_start_freeze_a_structured_declaration(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    assert _declare_variant(fixtures, experiment_id, _vpayload(version_id)).status_code == 201
    assert _declare_contract(fixtures, experiment_id, _structured(version_id)).status_code == 201
    authorization = _authorize(fixtures, experiment_id)
    assert authorization.status_code == 201, authorization.text
    revision = _structured(version_id, "COMPARATIVE", base_version=1)
    frozen = _declare_contract(fixtures, experiment_id, revision)
    assert frozen.status_code == 409 and _code(frozen) == "MEASUREMENT_CONTRACT_FROZEN_BY_AUTHORIZATION"
    started = _start(fixtures, experiment_id, authorization.json())
    assert started.status_code == 201, started.text
    permanent = _declare_contract(fixtures, experiment_id, {**revision, "client_request_id": uuid.uuid4().hex})
    assert permanent.status_code == 409 and _code(permanent) == "MEASUREMENT_CONTRACT_FROZEN_BY_EXECUTION_START"


# --- Evidence Claim structural compatibility --------------------------------------------------------------------------


def _structured_started(fixtures: dict, *, signals: list[dict] | None = None, structured: bool = True) -> dict:
    _strategy, _hypothesis, experiment_id = _experiment(fixtures)
    version = _declare_definition(fixtures, experiment_id, _definition_payload())
    version_id = version.json()["id"]
    assert _declare_variant(fixtures, experiment_id, _vpayload(version_id)).status_code == 201
    body = _structured(version_id, signals=signals) if structured else _payload(version_id)
    if structured and signals is None:
        body = _structured(version_id)
    assert _declare_contract(fixtures, experiment_id, body).status_code == 201
    authorization = _authorize(fixtures, experiment_id).json()
    started = _start(fixtures, experiment_id, authorization)
    assert started.status_code == 201, started.text
    all_signals = _signals(fixtures, experiment_id)
    return {
        "experiment_id": experiment_id,
        "start_id": started.json()["execution_start"]["id"],
        "signal_id": all_signals[0]["id"],
        "signals": all_signals,
    }


def test_a_bound_metric_claim_is_accepted_and_another_metric_is_a_typed_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _structured_started(fixtures)
    entry = _metric(fixtures)
    ok = _claim(fixtures, attempt, _claim_body(attempt, entry["id"], metric_name="clicks"))
    assert ok.status_code == 201, ok.text
    other = _claim(fixtures, attempt, _claim_body(attempt, entry["id"], metric_name="impressions"))
    assert other.status_code == 422 and _code(other) == "EVIDENCE_CLAIM_METRIC_NOT_BOUND"
    case = _claim(fixtures, attempt, _claim_body(attempt, entry["id"], metric_name="Clicks"))
    assert case.status_code == 422  # absent from the entry OR not the bound metric: never accepted


def test_an_exact_channel_binding_is_enforced_case_sensitively(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _structured_started(fixtures, signals=[_bound(binding="EXACT", channel="Instagram")])
    good = _metric(fixtures, channel="Instagram")
    bad = _metric(fixtures, channel="instagram", period_start="2026-02-01", period_end="2026-02-28")
    assert _claim(fixtures, attempt, _claim_body(attempt, good["id"])).status_code == 201
    rejected = _claim(fixtures, attempt, _claim_body(attempt, bad["id"]))
    assert rejected.status_code == 422 and _code(rejected) == "EVIDENCE_CLAIM_CHANNEL_NOT_BOUND"


def test_an_any_binding_accepts_any_channel(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _structured_started(fixtures)
    for index, channel in enumerate(("Instagram", "Email", "Other Channel")):
        entry = _metric(fixtures, channel=channel, period_start=f"2026-0{index + 1}-01", period_end=f"2026-0{index + 1}-28")
        assert _claim(fixtures, attempt, _claim_body(attempt, entry["id"])).status_code == 201


def test_a_legacy_contract_keeps_its_claim_behavior(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _structured_started(fixtures, structured=False)
    entry = _metric(fixtures)
    assert _claim(fixtures, attempt, _claim_body(attempt, entry["id"], metric_name="impressions")).status_code == 201
    assert _claim(fixtures, attempt, _claim_body(attempt, entry["id"], metric_name="clicks")).status_code == 201


# --- audit / surface / tenancy ------------------------------------------------------------------------------------------


def test_the_existing_lifecycle_events_are_the_only_audit_and_carry_no_declaration_content(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    assert _declare_contract(fixtures, experiment_id, _structured(version_id)).status_code == 201
    assert _declare_contract(fixtures, experiment_id, _structured(version_id, "COMPARATIVE", base_version=1)).status_code == 201
    events = _contract_audit(experiment_id)
    assert [(e["previous_state"], e["new_state"], e["actor_type"].value) for e in events] == [
        (None, "v1", "USER"), ("v1", "v2", "USER"),
    ]
    assert all(e["contract_id"] is not None for e in events)


def test_the_route_surface_is_unchanged() -> None:
    from app.main import create_app

    spec = create_app().openapi()["paths"]
    pairs = {(method.upper(), path) for path, item in spec.items() for method in item if method in {"get", "post", "put", "patch", "delete"}}
    assert len(pairs) == 112  # this capability adds no route
    base = "/api/v1/campaigns/{campaign_public_id}/experiments/{experiment_public_id}/measurement-contract"
    assert {pair for pair in pairs if "measurement-contract" in pair[1] or "declaration" in pair[1]} == {
        ("POST", base), ("GET", base), ("GET", base + "/history"),
    }


def test_a_structured_declaration_is_tenant_isolated(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    other_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    register_and_get_csrf(other_client, display_name="Other User")
    other_token = other_client.get("/api/v1/auth/csrf").json()["csrf_token"]
    other_campaign = other_client.post(
        "/api/v1/campaigns", json=campaign_payload(name="Other"), headers={"X-CSRF-Token": other_token}
    ).json()["campaign"]["id"]
    other = {**fixtures, "client": other_client, "csrf_token": other_token, "campaign_id": other_campaign}
    response = _declare_contract(other, experiment_id, _structured(version_id))
    assert response.status_code == 403 and _code(response) == "FORBIDDEN"
    assert _get_current(other, experiment_id).status_code == 403


def test_a_partially_structured_request_is_stopped_by_the_request_schema_first(campaign_run_client: dict) -> None:
    """Two layers run the same pure rule: the request schema (generic 422 VALIDATION_ERROR) answers first; the
    service's typed MEASUREMENT_CONTRACT_DECLARATION_INVALID is the defense for a direct service caller."""
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    response = _declare_contract(fixtures, experiment_id, _structured(version_id, declaration_semantics_version=None))
    assert response.status_code == 422 and _code(response) == "VALIDATION_ERROR"
    assert "declaration" in response.text.lower()


def test_the_authorization_shows_the_pinned_declaration_summary_and_no_new_pin(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    assert _declare_variant(fixtures, experiment_id, _vpayload(version_id)).status_code == 201
    contract = _declare_contract(fixtures, experiment_id, _structured(version_id, "COMPARATIVE"))
    assert contract.status_code == 201
    authorization = _authorize(fixtures, experiment_id)
    assert authorization.status_code == 201, authorization.text
    data = authorization.json()
    assert data["contract_version_id"] == contract.json()["id"]  # the Authorization pins the Contract version...
    assert (data["declaration_level"], data["declaration_semantics_version"]) == ("COMPARATIVE", 1)
    assert (data["measurement_window_days"], data["baseline_window_days"]) == (14, 7)
    assert not [key for key in data if "specification" in key or key.endswith("declaration_id")]  # ...and nothing else
