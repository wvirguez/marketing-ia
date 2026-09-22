"""Pins the frozen MEANING of ``declaration_semantics_version = 1`` and the pure
validation rules of the Pre-Execution Measurement Declaration — WITHOUT implementing
Measurement. Nothing here classifies a claim, counts a data point, pairs a period or
computes a difference: those are future Experiment Measurement. Pure (no database).
"""

from __future__ import annotations

import ast
import dataclasses
import inspect

import pytest

from app.strategy import measurement_declaration as declaration
from app.strategy.measurement_declaration import (
    SEMANTICS_V1,
    DeclarationSemanticsV1,
    declaration_shape_problems,
    first_binding_ownership_conflict,
    first_duplicate_binding_slot,
)


def test_semantics_version_one_meaning_is_pinned() -> None:
    assert declaration.DECLARATION_SEMANTICS_VERSION_1 == 1
    assert SEMANTICS_V1.anchor == "EXECUTION_START_ATTESTATION_STARTED_AT"  # anchor = ExecutionStartAttestation.started_at
    assert SEMANTICS_V1.window_unit == "ELAPSED_24_HOUR_DAYS" and SEMANTICS_V1.elapsed_day_hours == 24
    assert SEMANTICS_V1.timezone_policy == "NO_AUTHORITATIVE_TIMEZONE"
    assert (SEMANTICS_V1.civil_offset_min_hours, SEMANTICS_V1.civil_offset_max_hours) == (-12, 14)  # UTC-12..UTC+14
    assert SEMANTICS_V1.ambiguity_policy == "EXCLUDE_AND_DISCLOSE"
    assert SEMANTICS_V1.source_policy == "ANY_DISCLOSED"
    assert SEMANTICS_V1.any_channel_evaluation == "PER_SLICE"
    assert SEMANTICS_V1.descriptive_sufficiency_scope == "PER_SIGNAL_PER_CHANNEL_SLICE"
    assert SEMANTICS_V1.descriptive_aggregation == "NONE"
    assert SEMANTICS_V1.comparative_aggregation == "SINGLE_DATUM_PAIR"
    assert SEMANTICS_V1.comparative_pairing == "SAME_CHANNEL"
    assert SEMANTICS_V1.comparative_period_length == "EQUAL_INCLUSIVE"
    assert SEMANTICS_V1.best_pair_selection == "FORBIDDEN"
    assert SEMANTICS_V1.any_signal_level_coverage == "NONE"
    assert SEMANTICS_V1.tracking_validation == "NOT_ESTABLISHED"


def test_the_pinned_semantics_are_immutable_and_complete() -> None:
    assert isinstance(SEMANTICS_V1, DeclarationSemanticsV1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        SEMANTICS_V1.anchor = "OTHER"  # type: ignore[misc]
    assert {f.name for f in dataclasses.fields(DeclarationSemanticsV1)} == {
        "anchor", "window_unit", "elapsed_day_hours", "timezone_policy", "civil_offset_min_hours",
        "civil_offset_max_hours", "ambiguity_policy", "source_policy", "any_channel_evaluation",
        "descriptive_sufficiency_scope", "descriptive_aggregation", "comparative_aggregation", "comparative_pairing",
        "comparative_period_length", "best_pair_selection", "any_signal_level_coverage", "tracking_validation",
    }


def test_the_frozen_bounds_and_vocabularies_are_pinned() -> None:
    assert (declaration.STRUCTURED_WINDOW_DAYS_MIN, declaration.STRUCTURED_WINDOW_DAYS_MAX) == (4, 3650)
    assert declaration.DECLARATION_LEVELS == ("DESCRIPTIVE", "COMPARATIVE")
    assert declaration.CHANNEL_BINDINGS == ("ANY", "EXACT")
    assert declaration.BINDING_FIELDS == ("bound_metric_name", "channel_binding", "bound_channel", "min_data_points")
    assert declaration.DECLARATION_CONTRACT_FIELDS == (
        "declaration_level", "declaration_semantics_version", "baseline_window_days",
    )


def test_the_module_is_declaration_only_it_imports_no_measurement_engine() -> None:
    tree = ast.parse(inspect.getsource(declaration))
    imported = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [None])
        if (node.module if isinstance(node, ast.ImportFrom) else alias.name)
    }
    assert not {name for name in imported if name.startswith("app.")}, imported  # no app dependency at all
    public_functions = {
        n for n, o in vars(declaration).items()
        if inspect.isfunction(o) and not n.startswith("_") and o.__module__ == declaration.__name__
    }
    assert public_functions == {
        "is_structured", "declaration_shape_problems", "first_duplicate_binding_slot", "first_binding_ownership_conflict",
    }  # no classify/count/pair/aggregate/evaluate function exists


def _signal(**overrides):
    base = {"bound_metric_name": "clicks", "channel_binding": "ANY", "bound_channel": None, "min_data_points": 1}
    base.update(overrides)
    return base


def _problems(**overrides):
    kwargs = dict(
        declaration_level="DESCRIPTIVE", declaration_semantics_version=1, measurement_window_days=14,
        baseline_window_days=None, signals=[_signal()],
    )
    kwargs.update(overrides)
    return declaration_shape_problems(**kwargs)


def test_well_formed_declarations_have_no_problems() -> None:
    assert _problems() == []
    assert _problems(
        declaration_level="COMPARATIVE", baseline_window_days=7, signals=[_signal(min_data_points=None)]
    ) == []
    assert _problems(declaration_level=None, declaration_semantics_version=None, measurement_window_days=1,
                     signals=[_signal(bound_metric_name=None, channel_binding=None, min_data_points=None)]) == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"declaration_level": "OTHER"},
        {"declaration_semantics_version": 2},
        {"declaration_semantics_version": None},
        {"measurement_window_days": None},
        {"measurement_window_days": 3},
        {"measurement_window_days": 3651},
        {"measurement_window_days": True},
        {"baseline_window_days": 7},
        {"signals": [_signal(min_data_points=None)]},
        {"signals": [_signal(min_data_points=0)]},
        {"signals": [_signal(bound_metric_name=None)]},
        {"signals": [_signal(channel_binding=None)]},
        {"signals": [_signal(channel_binding="EXACT")]},
        {"signals": [_signal(bound_channel="email")]},
        {"declaration_level": "COMPARATIVE", "baseline_window_days": None, "signals": [_signal(min_data_points=None)]},
        {"declaration_level": "COMPARATIVE", "baseline_window_days": 3, "signals": [_signal(min_data_points=None)]},
        {"declaration_level": "COMPARATIVE", "baseline_window_days": 7},  # min_data_points forbidden
        {"declaration_level": None, "declaration_semantics_version": 1},
        {"declaration_level": None, "declaration_semantics_version": None, "measurement_window_days": 14},  # bound signal on a legacy level
    ],
)
def test_ill_formed_declarations_report_problems(overrides) -> None:
    assert _problems(**overrides)


def test_a_partially_structured_declaration_is_always_a_problem() -> None:
    assert _problems(declaration_level=None, declaration_semantics_version=None, signals=[_signal()])
    assert _problems(declaration_level=None, declaration_semantics_version=None, baseline_window_days=7,
                     signals=[_signal(bound_metric_name=None, channel_binding=None, min_data_points=None)])


def test_binding_slot_and_ownership_rules_are_exact_and_case_sensitive() -> None:
    any_a = _signal()
    exact_email = _signal(channel_binding="EXACT", bound_channel="email")
    exact_sms = _signal(channel_binding="EXACT", bound_channel="sms")
    assert first_duplicate_binding_slot([any_a, dict(any_a)]) == ("clicks", "ANY", "")
    assert first_duplicate_binding_slot([exact_email, dict(exact_email)]) == ("clicks", "EXACT", "email")
    assert first_duplicate_binding_slot([exact_email, exact_sms]) is None
    assert first_duplicate_binding_slot([exact_email, _signal(channel_binding="EXACT", bound_channel="Email")]) is None
    assert first_duplicate_binding_slot([_signal(bound_metric_name=None, channel_binding=None)] * 3) is None  # legacy

    assert first_binding_ownership_conflict([any_a, exact_email]) == "clicks"  # ANY then EXACT
    assert first_binding_ownership_conflict([exact_email, any_a]) == "clicks"  # EXACT then ANY
    assert first_binding_ownership_conflict([exact_email, exact_sms]) is None
    assert first_binding_ownership_conflict([any_a, _signal(bound_metric_name="reach", channel_binding="EXACT", bound_channel="email")]) is None
    assert first_binding_ownership_conflict([any_a, _signal(bound_metric_name="Clicks", channel_binding="EXACT", bound_channel="email")]) is None
    assert first_binding_ownership_conflict([{}, {}]) is None
