"""Structural database backstops for the Pre-Execution Measurement Declaration:
every test BYPASSES the service and inserts directly, proving the database itself —
not application code — rejects the violation. Also proves the binding-slot unique
expression index and that legacy all-NULL rows are unaffected. All marked `postgres`.

Honest limit, documented rather than hidden: the rules that span rows stay SERVICE-level
— that every signal of a STRUCTURED Contract is bound, that ``min_data_points`` matches
the level, that ANY and EXACT are never mixed for one metric, and that a structured
declaration is never attached to a CONTROLLED Definition. No trigger or extension is used.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from tests.strategytest import build_current_experiment
from tests.test_measurement_contract_db import _contract_row, _signal_row, _version, _violation

pytestmark = pytest.mark.postgres

_C = "ck_measurement_contract_versions_"
_S = "ck_measurement_contract_signals_"
_SLOT = "uq_contract_signals_binding_slot"


def _definition(db_session):
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    return _version(db_session, campaign, experiment, actor)


def _structured(definition, level="DESCRIPTIVE", **overrides):
    values = dict(
        declaration_level=level, declaration_semantics_version=1, measurement_window_days=14,
        baseline_window_days=7 if level == "COMPARATIVE" else None,
    )
    values.update(overrides)
    return _contract_row(definition, **values)


def _saved_contract(db_session, **overrides):
    definition = _definition(db_session)
    contract = _structured(definition, **overrides)
    db_session.add(contract)
    db_session.flush()
    return definition, contract


# --- Contract-level CHECKs ---------------------------------------------------------------------


def test_legacy_all_null_rows_satisfy_every_new_check(db_session) -> None:
    definition = _definition(db_session)
    contract = _contract_row(definition, measurement_window_days=1)  # legacy: any positive window, even < 4
    db_session.add(contract)
    db_session.flush()
    signal = _signal_row(contract)
    db_session.add(signal)
    db_session.flush()
    assert contract.declaration_level is None and signal.bound_metric_name is None


def test_a_valid_structured_descriptive_and_comparative_row_are_accepted(db_session) -> None:
    definition = _definition(db_session)
    db_session.add(_structured(definition, "DESCRIPTIVE", version=1))
    db_session.add(_structured(definition, "COMPARATIVE", version=2))
    db_session.flush()


@pytest.mark.parametrize(
    "overrides,constraint",
    [
        ({"declaration_level": "OTHER"}, "declaration_level_valid"),
        ({"declaration_level": None}, "level_version_copresent"),  # version=1 but no level
        ({"declaration_semantics_version": None}, "level_version_copresent"),  # level but no version
        ({"declaration_semantics_version": 2}, "semantics_version_v1"),
        ({"declaration_semantics_version": 0}, "semantics_version_v1"),
        ({"measurement_window_days": None}, "structured_window_bounds"),
        ({"measurement_window_days": 3}, "structured_window_bounds"),
        ({"measurement_window_days": 3651}, "structured_window_bounds"),
        ({"baseline_window_days": 7}, "baseline_iff_comparative"),  # DESCRIPTIVE with a baseline
    ],
)
def test_structured_contract_invariants_are_rejected_by_the_database(db_session, overrides, constraint) -> None:
    definition = _definition(db_session)
    assert _violation(db_session, _structured(definition, **overrides)) == _C + constraint


@pytest.mark.parametrize(
    "overrides,constraint",
    [
        ({"baseline_window_days": None}, "baseline_iff_comparative"),  # COMPARATIVE without a baseline
        ({"baseline_window_days": 3}, "baseline_window_bounds"),
        ({"baseline_window_days": 3651}, "baseline_window_bounds"),
    ],
)
def test_comparative_baseline_invariants_are_rejected_by_the_database(db_session, overrides, constraint) -> None:
    definition = _definition(db_session)
    assert _violation(db_session, _structured(definition, "COMPARATIVE", **overrides)) == _C + constraint


def test_a_legacy_row_may_not_carry_a_baseline(db_session) -> None:
    definition = _definition(db_session)
    assert _violation(db_session, _contract_row(definition, baseline_window_days=7)) == _C + "baseline_iff_comparative"


def test_the_window_and_baseline_bounds_are_inclusive_in_the_database(db_session) -> None:
    definition = _definition(db_session)
    db_session.add(_structured(definition, "COMPARATIVE", version=1, measurement_window_days=4, baseline_window_days=4))
    db_session.add(
        _structured(definition, "COMPARATIVE", version=2, measurement_window_days=3650, baseline_window_days=3650)
    )
    db_session.flush()


# --- Signal-level CHECKs -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides,constraint",
    [
        ({"bound_metric_name": "clicks"}, "binding_copresent"),  # metric without a binding
        ({"channel_binding": "ANY"}, "binding_copresent"),  # binding without a metric
        ({"bound_metric_name": "clicks", "channel_binding": "SOME"}, "channel_binding_valid"),
        ({"bound_metric_name": "clicks", "channel_binding": "ANY", "bound_channel": "email"}, "bound_channel_consistent"),
        ({"bound_metric_name": "clicks", "channel_binding": "EXACT"}, "bound_channel_consistent"),
        ({"bound_channel": "email"}, "bound_channel_consistent"),  # a channel with no binding at all
        ({"bound_metric_name": " ", "channel_binding": "ANY"}, "binding_text_nonblank_trimmed"),
        ({"bound_metric_name": " clicks", "channel_binding": "ANY"}, "binding_text_nonblank_trimmed"),
        ({"bound_metric_name": "clicks", "channel_binding": "EXACT", "bound_channel": ""}, "binding_text_nonblank_trimmed"),
        ({"bound_metric_name": "clicks", "channel_binding": "EXACT", "bound_channel": "email "}, "binding_text_nonblank_trimmed"),
        ({"bound_metric_name": "clicks", "channel_binding": "ANY", "min_data_points": 0}, "min_data_points_positive"),
        ({"bound_metric_name": "clicks", "channel_binding": "ANY", "min_data_points": -1}, "min_data_points_positive"),
        ({"min_data_points": 3}, "min_points_requires_binding"),
    ],
)
def test_signal_binding_invariants_are_rejected_by_the_database(db_session, overrides, constraint) -> None:
    _definition_row, contract = _saved_contract(db_session)
    assert _violation(db_session, _signal_row(contract, **overrides)) == _S + constraint


def test_valid_bindings_are_accepted(db_session) -> None:
    _d, contract = _saved_contract(db_session)
    db_session.add(_signal_row(contract, ordinal=1, name="a", bound_metric_name="clicks", channel_binding="ANY", min_data_points=1))
    db_session.add(
        _signal_row(contract, ordinal=2, name="b", bound_metric_name="reach", channel_binding="EXACT", bound_channel="email", min_data_points=5)
    )
    db_session.add(_signal_row(contract, ordinal=3, name="c", bound_metric_name="views", channel_binding="ANY"))  # COMPARATIVE style
    db_session.flush()


# --- binding-slot unique expression index ---------------------------------------------------------


def test_a_duplicate_any_binding_is_blocked_by_the_index(db_session) -> None:
    _d, contract = _saved_contract(db_session)
    db_session.add(_signal_row(contract, ordinal=1, name="a", bound_metric_name="clicks", channel_binding="ANY"))
    db_session.flush()
    assert _violation(
        db_session, _signal_row(contract, ordinal=2, name="b", bound_metric_name="clicks", channel_binding="ANY")
    ) == _SLOT


def test_a_duplicate_exact_binding_of_one_channel_is_blocked_by_the_index(db_session) -> None:
    _d, contract = _saved_contract(db_session)
    db_session.add(
        _signal_row(contract, ordinal=1, name="a", bound_metric_name="clicks", channel_binding="EXACT", bound_channel="email")
    )
    db_session.flush()
    assert _violation(
        db_session,
        _signal_row(contract, ordinal=2, name="b", bound_metric_name="clicks", channel_binding="EXACT", bound_channel="email"),
    ) == _SLOT


def test_exact_bindings_of_different_channels_are_allowed_and_channel_matching_is_case_sensitive(db_session) -> None:
    _d, contract = _saved_contract(db_session)
    for ordinal, channel in enumerate(("email", "sms", "Email"), start=1):
        db_session.add(
            _signal_row(
                contract, ordinal=ordinal, name=f"s{ordinal}", bound_metric_name="clicks",
                channel_binding="EXACT", bound_channel=channel,
            )
        )
    db_session.flush()


def test_the_same_slot_in_another_contract_version_is_allowed(db_session) -> None:
    definition = _definition(db_session)
    first = _structured(definition, version=1)
    second = _structured(definition, version=2)
    db_session.add_all([first, second])
    db_session.flush()
    for contract in (first, second):
        db_session.add(_signal_row(contract, bound_metric_name="clicks", channel_binding="ANY"))
    db_session.flush()


def test_legacy_null_bindings_never_collide(db_session) -> None:
    definition = _definition(db_session)
    contract = _contract_row(definition)
    db_session.add(contract)
    db_session.flush()
    for ordinal in range(1, 5):
        db_session.add(_signal_row(contract, ordinal=ordinal, name=f"legacy-{ordinal}"))
    db_session.flush()  # four all-NULL signals of one Contract: NULLs are distinct in the unique index


def test_the_index_is_a_plain_unique_expression_index_without_nulls_not_distinct(db_session) -> None:
    indexdef = db_session.execute(text("select indexdef from pg_indexes where indexname = :name"), {"name": _SLOT}).scalar_one()
    assert "UNIQUE" in indexdef and "COALESCE(bound_channel, ''" in indexdef
    assert "NULLS NOT DISTINCT" not in indexdef
    assert len(_SLOT) <= 63


def test_orm_metadata_and_the_created_schema_agree_on_the_new_objects(postgres_engine) -> None:
    inspector = inspect(postgres_engine)
    contract_columns = {c["name"]: c["nullable"] for c in inspector.get_columns("measurement_contract_versions")}
    signal_columns = {c["name"]: c["nullable"] for c in inspector.get_columns("measurement_contract_signals")}
    for name in ("declaration_level", "declaration_semantics_version", "baseline_window_days"):
        assert contract_columns[name] is True
    for name in ("bound_metric_name", "channel_binding", "bound_channel", "min_data_points"):
        assert signal_columns[name] is True
    assert {c["name"] for c in inspector.get_check_constraints("measurement_contract_versions")} >= {
        _C + n for n in (
            "declaration_level_valid", "level_version_copresent", "semantics_version_v1",
            "structured_window_bounds", "baseline_iff_comparative", "baseline_window_bounds",
        )
    }
    assert {c["name"] for c in inspector.get_check_constraints("measurement_contract_signals")} >= {
        _S + n for n in (
            "binding_copresent", "channel_binding_valid", "bound_channel_consistent",
            "binding_text_nonblank_trimmed", "min_data_points_positive", "min_points_requires_binding",
        )
    }
    assert _SLOT in {i["name"] for i in inspector.get_indexes("measurement_contract_signals")}
