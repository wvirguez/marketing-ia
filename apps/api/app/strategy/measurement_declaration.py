"""Pre-Execution Measurement Declaration — the frozen SEMANTICS-VERSION-1 meaning
and the pure structural validation of a structured declaration.

CAPABILITY: a ``MeasurementContractVersion`` (and its ``MeasurementContractRequiredSignal``
rows) may carry a STRUCTURED declaration, made and frozen BEFORE execution starts
(the existing C1 freeze — no second freeze mechanism), of how a future
Measurement WOULD read evidence. This module holds only

* the immutable constants that PIN what semantics version 1 means, and
* pure functions that decide whether a declaration is structurally complete and
  internally consistent.

DECLARATION != APPLICATION != MEASUREMENT != RESULT != VERDICT != LEARNING.
Nothing here executes, classifies, counts, pairs, aggregates or evaluates any
evidence, claim, metric entry or period: that is future Experiment Measurement.
A declaration is not pre-registration, does not validate evidence and does not
establish eligibility, sufficiency, success, attribution or causality.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

DECLARATION_LEVEL_DESCRIPTIVE = "DESCRIPTIVE"
DECLARATION_LEVEL_COMPARATIVE = "COMPARATIVE"
DECLARATION_LEVELS = (DECLARATION_LEVEL_DESCRIPTIVE, DECLARATION_LEVEL_COMPARATIVE)

CHANNEL_BINDING_ANY = "ANY"
CHANNEL_BINDING_EXACT = "EXACT"
CHANNEL_BINDINGS = (CHANNEL_BINDING_ANY, CHANNEL_BINDING_EXACT)

DECLARATION_SEMANTICS_VERSION_1 = 1

# SEMANTICS-VERSION-1 RULES (not universal domain truths, and never retrofitted
# onto legacy Contracts): the structured window and baseline bounds, in days.
STRUCTURED_WINDOW_DAYS_MIN = 4
STRUCTURED_WINDOW_DAYS_MAX = 3650

# Same widths as the columns the binding is compared against
# (``MetricValue.metric_name`` / ``MetricEntry.channel``).
BOUND_METRIC_NAME_MAX_LENGTH = 100
BOUND_CHANNEL_MAX_LENGTH = 100

BINDING_FIELDS = ("bound_metric_name", "channel_binding", "bound_channel", "min_data_points")
DECLARATION_CONTRACT_FIELDS = ("declaration_level", "declaration_semantics_version", "baseline_window_days")


@dataclass(frozen=True)
class DeclarationSemanticsV1:
    """The frozen MEANING of ``declaration_semantics_version = 1``. Each value is
    a label for a rule a FUTURE Measurement must apply; nothing in this
    repository applies any of them. Pinned by ``tests/test_measurement_declaration_semantics.py``."""

    # Anchor of every window: the human-attested start instant of the started attempt.
    anchor: str = "EXECUTION_START_ATTESTATION_STARTED_AT"
    # A window of N days is N elapsed 24-hour periods from the anchor, not N calendar dates.
    window_unit: str = "ELAPSED_24_HOUR_DAYS"
    elapsed_day_hours: int = 24
    # No authoritative workspace / campaign / source timezone is invented.
    timezone_policy: str = "NO_AUTHORITATIVE_TIMEZONE"
    # Conservative civil-offset envelope used to classify a date-only period against an instant.
    civil_offset_min_hours: int = -12
    civil_offset_max_hours: int = 14
    # A period that cannot be classified under every offset in the envelope.
    ambiguity_policy: str = "EXCLUDE_AND_DISCLOSE"
    # Any MetricSource may be used, and the sources used are disclosed.
    source_policy: str = "ANY_DISCLOSED"
    # A signal bound with ANY channel is evaluated separately for each channel slice.
    any_channel_evaluation: str = "PER_SLICE"
    # DESCRIPTIVE sufficiency: per signal and per channel slice, ``min_data_points``.
    descriptive_sufficiency_scope: str = "PER_SIGNAL_PER_CHANNEL_SLICE"
    descriptive_aggregation: str = "NONE"
    # COMPARATIVE: exactly one baseline datum and one observation datum per channel slice,
    # of equal inclusive period length, never paired across channels.
    comparative_aggregation: str = "SINGLE_DATUM_PAIR"
    comparative_pairing: str = "SAME_CHANNEL"
    comparative_period_length: str = "EQUAL_INCLUSIVE"
    best_pair_selection: str = "FORBIDDEN"
    # ANY yields no signal-level coverage boolean.
    any_signal_level_coverage: str = "NONE"
    # ``tracking_required`` stays declarative; tracking is never validated by a declaration.
    tracking_validation: str = "NOT_ESTABLISHED"


SEMANTICS_V1 = DeclarationSemanticsV1()


def is_structured(declaration_level: str | None) -> bool:
    return declaration_level is not None


def declaration_shape_problems(
    *,
    declaration_level: Any,
    declaration_semantics_version: Any,
    measurement_window_days: Any,
    baseline_window_days: Any,
    signals: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Every reason the declaration is not a well-formed LEGACY or STRUCTURED
    (DESCRIPTIVE / COMPARATIVE, semantics v1) declaration; empty when it is.
    Pure and row-local-plus-signal-list: it never reads a Definition, a claim or
    evidence. A partially structured declaration is always a problem."""
    problems: list[str] = []
    signal_list = list(signals)

    if declaration_level is None:
        if declaration_semantics_version is not None:
            problems.append("declaration_semantics_version requires declaration_level.")
        if baseline_window_days is not None:
            problems.append("baseline_window_days requires declaration_level COMPARATIVE.")
        for index, signal in enumerate(signal_list, start=1):
            if any(signal.get(field) is not None for field in BINDING_FIELDS):
                problems.append(f"Signal {index}: a signal binding requires a structured declaration_level.")
        return problems

    if declaration_level not in DECLARATION_LEVELS:
        return [f"declaration_level must be one of {', '.join(DECLARATION_LEVELS)}."]
    if declaration_semantics_version != DECLARATION_SEMANTICS_VERSION_1:
        problems.append(
            f"declaration_semantics_version must be {DECLARATION_SEMANTICS_VERSION_1} for a structured declaration."
        )
    if not _in_bounds(measurement_window_days):
        problems.append(
            f"measurement_window_days is required and must be between {STRUCTURED_WINDOW_DAYS_MIN} and "
            f"{STRUCTURED_WINDOW_DAYS_MAX} for a structured declaration."
        )

    if declaration_level == DECLARATION_LEVEL_COMPARATIVE:
        if not _in_bounds(baseline_window_days):
            problems.append(
                f"baseline_window_days is required and must be between {STRUCTURED_WINDOW_DAYS_MIN} and "
                f"{STRUCTURED_WINDOW_DAYS_MAX} for a COMPARATIVE declaration."
            )
    elif baseline_window_days is not None:
        problems.append("baseline_window_days is only allowed for a COMPARATIVE declaration.")

    for index, signal in enumerate(signal_list, start=1):
        problems.extend(_signal_problems(index, signal, declaration_level))
    return problems


def _in_bounds(value: Any) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and STRUCTURED_WINDOW_DAYS_MIN <= value <= STRUCTURED_WINDOW_DAYS_MAX
    )


def _signal_problems(index: int, signal: Mapping[str, Any], declaration_level: str) -> list[str]:
    prefix = f"Signal {index}: "
    problems: list[str] = []
    metric = signal.get("bound_metric_name")
    binding = signal.get("channel_binding")
    channel = signal.get("bound_channel")
    minimum = signal.get("min_data_points")

    if not metric:
        problems.append(prefix + "bound_metric_name is required for a structured declaration.")
    if binding not in CHANNEL_BINDINGS:
        problems.append(prefix + f"channel_binding is required and must be one of {', '.join(CHANNEL_BINDINGS)}.")
    elif binding == CHANNEL_BINDING_EXACT and not channel:
        problems.append(prefix + "bound_channel is required when channel_binding is EXACT.")
    elif binding == CHANNEL_BINDING_ANY and channel is not None:
        problems.append(prefix + "bound_channel is not allowed when channel_binding is ANY.")

    if declaration_level == DECLARATION_LEVEL_DESCRIPTIVE:
        if minimum is None or isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
            problems.append(prefix + "min_data_points (>= 1) is required for a DESCRIPTIVE declaration.")
    elif minimum is not None:
        problems.append(prefix + "min_data_points is not allowed for a COMPARATIVE declaration.")
    return problems


def first_duplicate_binding_slot(signals: Iterable[Mapping[str, Any]]) -> tuple[str, str, str] | None:
    """The first ``(metric, binding, channel-or-empty)`` slot declared twice in one
    Contract, else ``None``. Mirrors the database unique expression index
    (``uq_contract_signals_binding_slot``) so a duplicate is a typed domain
    rejection long before it could be a database error; the index remains the
    backstop. Exact, case-sensitive; unbound (legacy) signals never collide."""
    seen: set[tuple[str, str, str]] = set()
    for signal in signals:
        metric = signal.get("bound_metric_name")
        binding = signal.get("channel_binding")
        if metric is None or binding is None:
            continue
        slot = (metric, binding, signal.get("bound_channel") or "")
        if slot in seen:
            return slot
        seen.add(slot)
    return None


def first_binding_ownership_conflict(signals: Iterable[Mapping[str, Any]]) -> str | None:
    """The first metric name for which one Contract holds BOTH an ANY binding and
    an EXACT binding (in either order), else ``None``. Frozen invariant: per
    metric, EITHER one ANY binding OR one-or-more EXACT bindings with distinct
    channels, NEVER both. Service-enforced: no database trigger or extension.
    Exact, case-sensitive metric comparison; unbound (legacy) signals are ignored."""
    any_metrics: set[str] = set()
    exact_metrics: set[str] = set()
    for signal in signals:
        metric = signal.get("bound_metric_name")
        binding = signal.get("channel_binding")
        if metric is None:
            continue
        if binding == CHANNEL_BINDING_ANY:
            any_metrics.add(metric)
        elif binding == CHANNEL_BINDING_EXACT:
            exact_metrics.add(metric)
        if metric in any_metrics and metric in exact_metrics:
            return metric
    return None
