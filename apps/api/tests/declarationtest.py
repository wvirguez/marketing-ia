"""Shared helpers for Pre-Execution Measurement Declaration tests — real PostgreSQL
required. Every helper goes through the real production services (Definition,
Variant, Contract, Authorization, Start, Evidence Claim)."""

from __future__ import annotations

from tests.evidenceclaimtest import Started, start_authorization
from tests.strategytest import build_current_experiment
from tests.test_execution_authorization_domain import _authorize, _define, _declare_variant

from app.strategy.measurement_contract_service import ExperimentMeasurementContractService
from app.strategy.repository import MeasurementContractRepository


def bound_signal(
    name: str = "Click-through rate",
    metric: str | None = "clicks",
    binding: str | None = "ANY",
    channel: str | None = None,
    min_points: int | None = 1,
    **overrides: object,
) -> dict:
    payload = {
        "name": name,
        "description": f"{name}.",
        "expected_direction": None,
        "evidence_requirement": None,
        "tracking_required": False,
        "bound_metric_name": metric,
        "channel_binding": binding,
        "bound_channel": channel,
        "min_data_points": min_points,
    }
    payload.update(overrides)
    return payload


def legacy_signal(name: str = "Click-through rate", **overrides: object) -> dict:
    return bound_signal(name, metric=None, binding=None, channel=None, min_points=None, **overrides)


def declare(
    session,
    campaign,
    experiment,
    actor,
    version,
    *,
    signals,
    level: str | None = "DESCRIPTIVE",
    semantics_version: int | None = 1,
    window: int | None = 14,
    baseline: int | None = None,
    key: str = "c-1",
    base_version: int = 0,
    **extra: object,
):
    """The full ``declare_or_revise`` result ``(row, signals, definition, created)``."""
    return ExperimentMeasurementContractService(session).declare_or_revise(
        campaign=campaign,
        experiment_public_id=experiment.public_id,
        base_version=base_version,
        client_request_id=key,
        definition_version_public_id=version.public_id,
        measurement_window_days=window,
        minimum_evidence=extra.pop("minimum_evidence", None),
        success_criterion=extra.pop("success_criterion", None),
        analysis_method_intent=extra.pop("analysis_method_intent", None),
        stopping_rule=extra.pop("stopping_rule", None),
        decision_rule_intent=extra.pop("decision_rule_intent", None),
        declaration_level=level,
        declaration_semantics_version=semantics_version if level is not None else None,
        baseline_window_days=baseline,
        signals=signals,
        actor_user_id=actor.id,
        **extra,
    )


def descriptive_signals() -> list[dict]:
    return [bound_signal()]


def comparative_signals() -> list[dict]:
    return [bound_signal(min_points=None)]


def build_experiment_with_definition(session, *, campaign_name: str = "Declaration Campaign", **definition_overrides):
    campaign, _strategy, _hypothesis, experiment, actor = build_current_experiment(session, campaign_name=campaign_name)
    version = _define(session, campaign, experiment, actor, key="d-1", **definition_overrides)
    return campaign, experiment, actor, version


def build_structured_started(
    session,
    *,
    campaign_name: str = "Declaration Claim Campaign",
    level: str = "DESCRIPTIVE",
    signals: list[dict] | None = None,
    window: int = 14,
    baseline: int | None = None,
) -> Started:
    """A STARTED attempt whose pinned Contract carries a STRUCTURED declaration."""
    campaign, experiment, actor, version = build_experiment_with_definition(session, campaign_name=campaign_name)
    _declare_variant(session, campaign, experiment, actor, version, label="A", key="v-1")
    if signals is None:
        signals = descriptive_signals() if level == "DESCRIPTIVE" else comparative_signals()
    contract = declare(
        session, campaign, experiment, actor, version, signals=signals, level=level, window=window,
        baseline=(baseline if baseline is not None else 7) if level == "COMPARATIVE" else None, key="c-1",
    )[0]
    authorization, _snapshot, _created = _authorize(session, campaign, experiment, actor, key="a-1")
    assert authorization.contract_version_id == contract.id
    _a, start, _c = start_authorization(session, campaign, experiment, actor, authorization, key="s-1")
    rows = MeasurementContractRepository(session).list_signals_for_version(contract_version_id=contract.id)
    return Started(campaign, experiment, actor, version, authorization, start, rows)
