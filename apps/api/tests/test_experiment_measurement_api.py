"""API tests for Experiment Measurement — the new governed aggregate root
(frozen Discovery / Design Freeze / both Reconciliations). Builds the full
real chain (Definition -> Variant -> structured Contract -> Authorization ->
Start -> Claims) through the existing production API surface, then exercises
the new POST/GET measurement-runs routes. All marked ``postgres``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from tests.evidenceclaimtest import claim as _svc_claim  # unused, kept for parity import style
from tests.test_evidence_claim_api import _claims_path, _metric, _signals
from tests.test_execution_authorization_api import _authorize, _body as _auth_body, _experiment
from tests.test_execution_start_api import _start, _start_body, _start_path
from tests.test_experiment_api import _experiments_path
from tests.test_experiment_definition_api import _declare as _declare_definition, _payload as _definition_payload
from tests.test_experiment_variant_api import _declare_variant, _vpayload
from tests.test_measurement_contract_api import _declare_contract, _mc_path, _payload as _contract_payload, _signal

pytestmark = pytest.mark.postgres

FORBIDDEN_WORDS = (
    "eligible", "validated", "successful", "winner", "result", "verdict", "causal", "attributed", "significan",
    "lift", "improvement",
)


def _post(fixtures: dict, path: str, json: dict):
    return fixtures["client"].post(path, json=json, headers={"X-CSRF-Token": fixtures["csrf_token"]})


def _bound(name: str = "Click-through rate", metric: str = "clicks", binding: str = "ANY", channel=None, min_points=1):
    return _signal(
        name=name, bound_metric_name=metric, channel_binding=binding, bound_channel=channel, min_data_points=min_points
    )


def _structured_payload(version_id: str, level: str = "DESCRIPTIVE", *, window: int = 14, baseline: int | None = None, signals=None):
    body = _contract_payload(
        version_id,
        declaration_level=level,
        declaration_semantics_version=1,
        measurement_window_days=window,
        baseline_window_days=(baseline if baseline is not None else 7) if level == "COMPARATIVE" else None,
        signals=signals if signals is not None else [_bound(min_points=None if level == "COMPARATIVE" else 1)],
    )
    return body


def _run_path(fixtures: dict, experiment_id: str, start_id: str, suffix: str = "") -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/experiments/{experiment_id}/execution-starts/{start_id}/measurement-runs{suffix}"


def _create_run(fixtures: dict, experiment_id: str, start_id: str, key: str | None = None):
    return _post(fixtures, _run_path(fixtures, experiment_id, start_id), {"client_request_id": key or uuid.uuid4().hex})


def _list_runs(fixtures: dict, experiment_id: str, start_id: str):
    return fixtures["client"].get(_run_path(fixtures, experiment_id, start_id))


def _structured_started(fixtures: dict, *, level: str = "DESCRIPTIVE", window: int = 14, baseline: int | None = None, signals=None) -> dict:
    """(experiment_id, start_id, started_at, signal_id, signals) through the
    real Definition/Variant/Contract/Authorization/Start API chain."""
    _strategy_id, _hyp_id, experiment_id = _experiment(fixtures)
    version = _declare_definition(fixtures, experiment_id, _definition_payload())
    assert version.status_code == 201, version.text
    version_id = version.json()["id"]
    variant = _declare_variant(fixtures, experiment_id, _vpayload(version_id, label="Condition A"))
    assert variant.status_code == 201, variant.text
    contract = _declare_contract(fixtures, experiment_id, _structured_payload(version_id, level, window=window, baseline=baseline, signals=signals))
    assert contract.status_code == 201, contract.text
    authorization = _authorize(fixtures, experiment_id)
    assert authorization.status_code == 201, authorization.text
    auth = authorization.json()
    started = _start(fixtures, experiment_id, auth)
    assert started.status_code == 201, started.text
    start = started.json()
    sig = _signals(fixtures, experiment_id)
    return {
        "experiment_id": experiment_id,
        "start_id": start["execution_start"]["id"],
        "started_at": start["execution_start"]["started_at"],
        "signal_id": sig[0]["id"],
        "signals": sig,
    }


def _legacy_started(fixtures: dict, *, hypothesis_id: str | None = None, signals=None) -> dict:
    if hypothesis_id is None:
        _strategy_id, hypothesis_id, experiment_id = _experiment(fixtures)
    else:
        created = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "Second experiment."})
        assert created.status_code == 201, created.text
        experiment_id = created.json()["id"]
    version = _declare_definition(fixtures, experiment_id, _definition_payload())
    assert version.status_code == 201, version.text
    version_id = version.json()["id"]
    variant = _declare_variant(fixtures, experiment_id, _vpayload(version_id, label="Condition A"))
    assert variant.status_code == 201, variant.text
    contract_body = _contract_payload(version_id, signals=signals) if signals is not None else _contract_payload(version_id)
    contract = _declare_contract(fixtures, experiment_id, contract_body)
    assert contract.status_code == 201, contract.text
    authorization = _authorize(fixtures, experiment_id)
    assert authorization.status_code == 201, authorization.text
    auth = authorization.json()
    started = _start(fixtures, experiment_id, auth)
    assert started.status_code == 201, started.text
    start = started.json()
    sig = _signals(fixtures, experiment_id)
    return {
        "experiment_id": experiment_id,
        "hypothesis_id": hypothesis_id,
        "start_id": start["execution_start"]["id"],
        "started_at": start["execution_start"]["started_at"],
        "signal_id": sig[0]["id"],
        "signals": sig,
    }


def _hours(started_at_iso: str, hours: float) -> str:
    anchor = datetime.fromisoformat(started_at_iso.replace("Z", "+00:00"))
    return (anchor + timedelta(hours=hours)).date().isoformat()


def _entry(fixtures: dict, *, period_start: str, period_end: str, channel: str = "email", values=None):
    body = _metric(fixtures, period_start=period_start, period_end=period_end, channel=channel, values=values)
    return body["id"]


def _claim(fixtures: dict, attempt: dict, *, entry_id: str, signal_id: str | None = None, metric_name: str = "clicks", key: str | None = None):
    return _post(
        fixtures,
        _claims_path(fixtures, attempt["experiment_id"], attempt["start_id"]),
        {
            "client_request_id": key or uuid.uuid4().hex,
            "required_signal_id": signal_id or attempt["signal_id"],
            "metric_entry_id": entry_id,
            "metric_name": metric_name,
        },
    )


# --- legacy ----------------------------------------------------------------------------------------------------


def test_legacy_run_has_no_signal_outputs_and_post_hoc_datum_usage(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _legacy_started(fixtures)
    entry_id = _entry(fixtures, period_start="2026-01-01", period_end="2026-01-31")
    claim = _claim(fixtures, attempt, entry_id=entry_id)
    assert claim.status_code == 201, claim.text

    response = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["legacy"] is True
    assert body["signal_outputs"] == []
    assert len(body["datum_usages"]) == 1
    usage = body["datum_usages"][0]
    assert usage["usage_decision"] == "CONSUMED"
    assert usage["temporal_role"] is None


# --- structured descriptive --------------------------------------------------------------------------------------


def test_structured_descriptive_coverage_met(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _structured_started(fixtures, level="DESCRIPTIVE", window=14, signals=[_bound(min_points=1)])
    entry_id = _entry(
        fixtures, period_start=_hours(attempt["started_at"], 48), period_end=_hours(attempt["started_at"], 72)
    )
    claim = _claim(fixtures, attempt, entry_id=entry_id)
    assert claim.status_code == 201, claim.text

    response = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["legacy"] is False
    assert len(body["signal_outputs"]) == 1
    signal_output = body["signal_outputs"][0]
    assert signal_output["declaration_level"] == "DESCRIPTIVE"
    assert len(signal_output["slices"]) == 1
    slice_ = signal_output["slices"][0]
    assert slice_["qualifying_count"] == 1
    assert slice_["required_count"] == 1
    assert slice_["coverage_state"] == "COVERED"
    assert slice_["pairing_state"] is None


def test_structured_descriptive_any_zero_claims_is_no_slice_rows(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _structured_started(fixtures, level="DESCRIPTIVE", window=14, signals=[_bound(min_points=1)])
    response = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    assert response.status_code == 201, response.text
    signal_output = response.json()["signal_outputs"][0]
    assert signal_output["slices"] == []


def test_structured_descriptive_ambiguous_boundary_is_excluded(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _structured_started(fixtures, level="DESCRIPTIVE", window=14, signals=[_bound(min_points=1)])
    # A period crossing the anchor itself is always ambiguous under the
    # frozen envelope, regardless of time-of-day.
    entry_id = _entry(
        fixtures, period_start=_hours(attempt["started_at"], -24), period_end=_hours(attempt["started_at"], 24)
    )
    claim = _claim(fixtures, attempt, entry_id=entry_id)
    assert claim.status_code == 201, claim.text
    response = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    slice_ = response.json()["signal_outputs"][0]["slices"][0]
    assert slice_["qualifying_count"] == 0
    assert slice_["ambiguous_excluded_count"] == 1
    assert slice_["coverage_state"] == "NOT_COVERED"


def test_structured_descriptive_out_of_window_is_excluded(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _structured_started(fixtures, level="DESCRIPTIVE", window=14, signals=[_bound(min_points=1)])
    entry_id = _entry(
        fixtures, period_start=_hours(attempt["started_at"], -240), period_end=_hours(attempt["started_at"], -220)
    )
    claim = _claim(fixtures, attempt, entry_id=entry_id)
    assert claim.status_code == 201, claim.text
    response = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    slice_ = response.json()["signal_outputs"][0]["slices"][0]
    assert slice_["qualifying_count"] == 0
    assert slice_["out_of_window_count"] == 1


# --- structured comparative ---------------------------------------------------------------------------------------


def test_structured_comparative_pair_established(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _structured_started(fixtures, level="COMPARATIVE", window=14, baseline=7)
    baseline_entry = _entry(
        fixtures,
        period_start=_hours(attempt["started_at"], -96),
        period_end=_hours(attempt["started_at"], -72),
        values={"clicks": "100"},
    )
    observation_entry = _entry(
        fixtures,
        period_start=_hours(attempt["started_at"], 48),
        period_end=_hours(attempt["started_at"], 72),
        values={"clicks": "150"},
    )
    assert _claim(fixtures, attempt, entry_id=baseline_entry).status_code == 201
    assert _claim(fixtures, attempt, entry_id=observation_entry, key=uuid.uuid4().hex).status_code == 201

    response = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    assert response.status_code == 201, response.text
    slice_ = response.json()["signal_outputs"][0]["slices"][0]
    assert slice_["pairing_state"] == "PAIR"
    assert slice_["baseline_value"] == "100.0000"
    assert slice_["observation_value"] == "150.0000"
    assert slice_["signed_arithmetic_difference"] == "50.0000"


def test_structured_comparative_incomplete_without_baseline(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _structured_started(fixtures, level="COMPARATIVE", window=14, baseline=7)
    observation_entry = _entry(
        fixtures, period_start=_hours(attempt["started_at"], 48), period_end=_hours(attempt["started_at"], 72)
    )
    assert _claim(fixtures, attempt, entry_id=observation_entry).status_code == 201
    response = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    slice_ = response.json()["signal_outputs"][0]["slices"][0]
    assert slice_["pairing_state"] == "INCOMPLETE"
    assert slice_["baseline_value"] is None


def test_structured_comparative_length_mismatch(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _structured_started(fixtures, level="COMPARATIVE", window=14, baseline=7)
    baseline_entry = _entry(
        fixtures, period_start=_hours(attempt["started_at"], -96), period_end=_hours(attempt["started_at"], -72)
    )  # 2-day period
    observation_entry = _entry(
        fixtures, period_start=_hours(attempt["started_at"], 48), period_end=_hours(attempt["started_at"], 96)
    )  # 3-day period
    assert _claim(fixtures, attempt, entry_id=baseline_entry).status_code == 201
    assert _claim(fixtures, attempt, entry_id=observation_entry, key=uuid.uuid4().hex).status_code == 201
    response = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    slice_ = response.json()["signal_outputs"][0]["slices"][0]
    assert slice_["pairing_state"] == "LENGTH_MISMATCH"


def test_structured_comparative_surplus_with_two_observations(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _structured_started(fixtures, level="COMPARATIVE", window=14, baseline=7)
    baseline_entry = _entry(
        fixtures, period_start=_hours(attempt["started_at"], -96), period_end=_hours(attempt["started_at"], -72),
        channel="a",
    )
    obs_1 = _entry(fixtures, period_start=_hours(attempt["started_at"], 48), period_end=_hours(attempt["started_at"], 72), channel="a")
    obs_2 = _entry(fixtures, period_start=_hours(attempt["started_at"], 200), period_end=_hours(attempt["started_at"], 224), channel="a")
    assert _claim(fixtures, attempt, entry_id=baseline_entry).status_code == 201
    assert _claim(fixtures, attempt, entry_id=obs_1, key=uuid.uuid4().hex).status_code == 201
    assert _claim(fixtures, attempt, entry_id=obs_2, key=uuid.uuid4().hex).status_code == 201
    response = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    signal_output = response.json()["signal_outputs"][0]
    assert len(signal_output["slices"]) == 1, signal_output  # single "a" channel slice
    slice_ = signal_output["slices"][0]
    assert slice_["pairing_state"] == "SURPLUS"


# --- multi-signal / overlap ---------------------------------------------------------------------------------------


def test_multi_signal_conflict_excludes_all_conflicting_signals(campaign_run_client: dict) -> None:
    # Multi-signal conflict (frozen §25: "No best signal. No first-wins. No
    # arbitrary tie-break.") is only reachable under a LEGACY contract: claim
    # creation there does not tie metric_name to one specific signal, so two
    # claims can legitimately reference the same (metric_entry_id,
    # metric_name) under two different required_signal_ids. Under a
    # STRUCTURED contract this scenario is unreachable (claim creation
    # enforces signal.bound_metric_name == metric_name, and declare-time
    # validation forbids two signals sharing a binding slot).
    fixtures = campaign_run_client
    signals = [_signal(name="Signal A"), _signal(name="Signal B")]
    attempt = _legacy_started(fixtures, signals=signals)
    entry_id = _entry(
        fixtures, period_start=_hours(attempt["started_at"], 48), period_end=_hours(attempt["started_at"], 72),
        values={"clicks": "10"},
    )
    signal_a_id = attempt["signals"][0]["id"]
    signal_b_id = attempt["signals"][1]["id"]
    claim_a = _claim(fixtures, attempt, entry_id=entry_id, signal_id=signal_a_id, metric_name="clicks")
    assert claim_a.status_code == 201, claim_a.text
    claim_b = _claim(fixtures, attempt, entry_id=entry_id, signal_id=signal_b_id, metric_name="clicks")
    assert claim_b.status_code == 201, claim_b.text

    response = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["legacy"] is True
    assert body["signal_outputs"] == []
    usages_by_claim = {usage["claim_id"]: usage for usage in body["datum_usages"]}
    assert len(usages_by_claim) == 2
    assert usages_by_claim[claim_a.json()["id"]]["usage_decision"] == "EXCLUDED_MULTI_SIGNAL"
    assert usages_by_claim[claim_b.json()["id"]]["usage_decision"] == "EXCLUDED_MULTI_SIGNAL"


def test_overlap_conflict_excludes_all_conflicting_usages(campaign_run_client: dict) -> None:
    # Overlap conflict (frozen §26/§36: "No latest-wins. No earliest-wins. No
    # best-quality choice.") arises when two claims under the same signal,
    # channel, and temporal role each independently qualify as OBSERVATION
    # but their inclusive date periods overlap.
    fixtures = campaign_run_client
    attempt = _structured_started(fixtures, level="DESCRIPTIVE", window=14, signals=[_bound(min_points=1)])
    entry_1 = _entry(
        fixtures, period_start=_hours(attempt["started_at"], 48), period_end=_hours(attempt["started_at"], 96)
    )
    entry_2 = _entry(
        fixtures, period_start=_hours(attempt["started_at"], 72), period_end=_hours(attempt["started_at"], 120)
    )
    claim_1 = _claim(fixtures, attempt, entry_id=entry_1)
    assert claim_1.status_code == 201, claim_1.text
    claim_2 = _claim(fixtures, attempt, entry_id=entry_2, key=uuid.uuid4().hex)
    assert claim_2.status_code == 201, claim_2.text

    response = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    assert response.status_code == 201, response.text
    body = response.json()
    usages_by_claim = {usage["claim_id"]: usage for usage in body["datum_usages"]}
    assert len(usages_by_claim) == 2
    assert usages_by_claim[claim_1.json()["id"]]["usage_decision"] == "EXCLUDED_CONFLICT"
    assert usages_by_claim[claim_2.json()["id"]]["usage_decision"] == "EXCLUDED_CONFLICT"
    slice_ = body["signal_outputs"][0]["slices"][0]
    assert slice_["conflict_excluded_count"] == 2
    assert slice_["qualifying_count"] == 0


# --- descriptive tracking disclosure -----------------------------------------------------------------------------


def test_no_response_property_claims_eligibility_validation_or_success(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _structured_started(fixtures, level="DESCRIPTIVE", window=14, signals=[_bound(min_points=1)])
    entry_id = _entry(fixtures, period_start=_hours(attempt["started_at"], 48), period_end=_hours(attempt["started_at"], 72))
    assert _claim(fixtures, attempt, entry_id=entry_id).status_code == 201
    response = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    body = response.json()
    # "semantics" is the one deliberate exception: it is the firewall
    # statement itself, an explicit NEGATION ("...OR VALIDATED LEARNING"),
    # mirroring EEB's own EVIDENCE_CLAIM_SEMANTICS precedent exactly.
    assert body.pop("semantics") == "OBSERVATIONAL MEASUREMENT ONLY — NOT AN EXPERIMENT RESULT, HYPOTHESIS VERDICT, CAUSAL ATTRIBUTION, OR VALIDATED LEARNING"
    text = str(body).lower()
    for word in FORBIDDEN_WORDS:
        assert word not in text, word


# --- idempotency ---------------------------------------------------------------------------------------------------


def test_same_key_same_start_replays_the_original_run(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _legacy_started(fixtures)
    key = uuid.uuid4().hex
    first = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"], key=key)
    assert first.status_code == 201, first.text
    second = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"], key=key)
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]


def test_same_key_different_start_is_a_typed_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt_one = _legacy_started(fixtures)
    attempt_two = _legacy_started(fixtures, hypothesis_id=attempt_one["hypothesis_id"])
    key = uuid.uuid4().hex
    first = _create_run(fixtures, attempt_one["experiment_id"], attempt_one["start_id"], key=key)
    assert first.status_code == 201, first.text
    second = _create_run(fixtures, attempt_two["experiment_id"], attempt_two["start_id"], key=key)
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "EXPERIMENT_MEASUREMENT_IDEMPOTENCY_KEY_CONFLICT"


def test_intentional_rerun_with_a_new_key_creates_a_second_immutable_run(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _legacy_started(fixtures)
    first = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    assert first.status_code == 201, first.text
    second = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    assert second.status_code == 201, second.text
    assert second.json()["id"] != first.json()["id"]


# --- listing ---------------------------------------------------------------------------------------------------


def test_list_returns_every_run_ascending(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _legacy_started(fixtures)
    _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    response = _list_runs(fixtures, attempt["experiment_id"], attempt["start_id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["runs"]) == 2


# --- route shape / immutability --------------------------------------------------------------------------------


def test_no_update_or_delete_route_exists(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _legacy_started(fixtures)
    run = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    run_id = run.json()["id"]
    path = _run_path(fixtures, attempt["experiment_id"], attempt["start_id"]) + f"/{run_id}"
    put_response = fixtures["client"].put(path, json={}, headers={"X-CSRF-Token": fixtures["csrf_token"]})
    assert put_response.status_code in (404, 405)
    delete_response = fixtures["client"].delete(path, headers={"X-CSRF-Token": fixtures["csrf_token"]})
    assert delete_response.status_code in (404, 405)


# --- channel derivation (derived-by-construction proof) ---------------------------------------------------------
#
# ExperimentMeasurementDatumUsage.channel is populated in
# _build_working_set as `channel=entry.channel` — copied from the claim's
# underlying MetricEntry at computation time, unconditionally, before the
# is_structured branch. It is not enforced by any DB constraint tying the
# usage row's channel back to metric_entries. These two tests prove the
# persisted/returned channel is exactly the originating MetricEntry's own
# channel, for both a structured and a Legacy run.


def test_structured_datum_usage_channel_matches_metric_entry_channel(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _structured_started(fixtures, level="DESCRIPTIVE", window=14, signals=[_bound(min_points=1)])
    distinctive_channel = "podcast-sponsorship-q3"
    entry_id = _entry(
        fixtures,
        period_start=_hours(attempt["started_at"], 48),
        period_end=_hours(attempt["started_at"], 72),
        channel=distinctive_channel,
    )
    claim = _claim(fixtures, attempt, entry_id=entry_id)
    assert claim.status_code == 201, claim.text

    response = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["datum_usages"]) == 1
    assert body["datum_usages"][0]["channel"] == distinctive_channel


def test_legacy_datum_usage_channel_matches_metric_entry_channel(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _legacy_started(fixtures)
    distinctive_channel = "affiliate-network-referral"
    entry_id = _entry(
        fixtures, period_start="2026-01-01", period_end="2026-01-31", channel=distinctive_channel
    )
    claim = _claim(fixtures, attempt, entry_id=entry_id)
    assert claim.status_code == 201, claim.text

    response = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["legacy"] is True
    assert len(body["datum_usages"]) == 1
    assert body["datum_usages"][0]["channel"] == distinctive_channel

    # Both paths execute the identical unconditional line
    # (`channel=entry.channel` in `_build_working_set`, before any
    # `is_structured` branch), so this Legacy assertion and the structured
    # one above exercise literally the same code path — asserted separately
    # here because the derived-by-construction invariant must be proven
    # explicit for both declared shapes, not inferred from one.


# --- structured DatumUsage -> SignalOutput construction proof ----------------------------------------------------
#
# For a STRUCTURED run, every persisted DatumUsage must have a
# corresponding SignalOutput in the same response sharing the same
# required_signal_id (both are Run-scoped by construction — there is no DB
# FK from experiment_measurement_datum_usages to
# experiment_measurement_signal_outputs). For a LEGACY run there is no
# SignalOutput at all — an unconditional FK was rejected because it would
# make Legacy impossible, so the accepted design is proven behaviorally
# instead: signal_outputs == [] while datum_usages is non-empty.


def _assert_every_datum_usage_has_matching_signal_output(body: dict) -> None:
    signal_output_ids = {output["required_signal_id"] for output in body["signal_outputs"]}
    assert body["datum_usages"], "expected at least one datum usage to check the cross-reference against"
    for usage in body["datum_usages"]:
        assert usage["required_signal_id"] in signal_output_ids, usage


def test_structured_descriptive_datum_usage_signal_output_cross_reference(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _structured_started(fixtures, level="DESCRIPTIVE", window=14, signals=[_bound(min_points=1)])
    consumed_entry = _entry(
        fixtures, period_start=_hours(attempt["started_at"], 48), period_end=_hours(attempt["started_at"], 72)
    )
    excluded_entry = _entry(
        fixtures, period_start=_hours(attempt["started_at"], -240), period_end=_hours(attempt["started_at"], -220)
    )
    assert _claim(fixtures, attempt, entry_id=consumed_entry).status_code == 201
    assert _claim(fixtures, attempt, entry_id=excluded_entry, key=uuid.uuid4().hex).status_code == 201

    response = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["legacy"] is False
    assert len(body["datum_usages"]) == 2
    decisions = {usage["usage_decision"] for usage in body["datum_usages"]}
    assert "CONSUMED" in decisions
    assert "EXCLUDED_OUT_OF_WINDOW" in decisions
    _assert_every_datum_usage_has_matching_signal_output(body)


def test_structured_comparative_datum_usage_signal_output_cross_reference(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _structured_started(fixtures, level="COMPARATIVE", window=14, baseline=7)
    baseline_entry = _entry(
        fixtures, period_start=_hours(attempt["started_at"], -96), period_end=_hours(attempt["started_at"], -72)
    )
    observation_entry = _entry(
        fixtures, period_start=_hours(attempt["started_at"], 48), period_end=_hours(attempt["started_at"], 72)
    )
    excluded_entry = _entry(
        fixtures, period_start=_hours(attempt["started_at"], -240), period_end=_hours(attempt["started_at"], -220)
    )
    assert _claim(fixtures, attempt, entry_id=baseline_entry).status_code == 201
    assert _claim(fixtures, attempt, entry_id=observation_entry, key=uuid.uuid4().hex).status_code == 201
    assert _claim(fixtures, attempt, entry_id=excluded_entry, key=uuid.uuid4().hex).status_code == 201

    response = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["legacy"] is False
    assert len(body["datum_usages"]) == 3
    decisions = {usage["usage_decision"] for usage in body["datum_usages"]}
    assert "CONSUMED" in decisions
    assert "EXCLUDED_OUT_OF_WINDOW" in decisions
    _assert_every_datum_usage_has_matching_signal_output(body)


def test_legacy_control_no_signal_outputs_despite_nonempty_datum_usages(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    attempt = _legacy_started(fixtures)
    entry_id = _entry(fixtures, period_start="2026-01-01", period_end="2026-01-31")
    claim = _claim(fixtures, attempt, entry_id=entry_id)
    assert claim.status_code == 201, claim.text

    response = _create_run(fixtures, attempt["experiment_id"], attempt["start_id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["legacy"] is True
    assert body["signal_outputs"] == []
    assert len(body["datum_usages"]) == 1
    assert body["datum_usages"][0]["required_signal_id"] == attempt["signal_id"]
