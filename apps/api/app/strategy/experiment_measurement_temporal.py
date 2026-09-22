"""Experiment Measurement — the frozen SEMANTICS-VERSION-1 temporal
classification formulas (Design Freeze §P/§Q, unchanged through both
reconciliation gates). Pure functions only: no database access, no ORM
import, no side effect. This module answers exactly one question per call —
"what temporal class does this claimed period belong to, given this Start's
anchor?" — and nothing more. Coverage, pairing, conflict resolution and
persistence all live in ``ExperimentMeasurementService``.

DECLARATION != APPLICATION: this module IS the application of PEMD's frozen
semantics-v1 rules, and it is deliberately the only place in this codebase
where that classification is actually computed — before this module existed,
only the rule LABELS were pinned (``app/strategy/measurement_declaration.py``).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

QUALIFYING = "QUALIFYING"
QUALIFYING_BASELINE = "QUALIFYING_BASELINE"
QUALIFYING_OBSERVATION = "QUALIFYING_OBSERVATION"
OUT_OF_WINDOW = "OUT_OF_WINDOW"
EXCLUDED_AMBIGUOUS = "EXCLUDED_AMBIGUOUS"

# The conservative civil-offset envelope (frozen PEMD semantics v1): every
# possible UTC offset in actual use, UTC-12 through UTC+14.
_ENVELOPE_LOW_HOURS = 12
_ENVELOPE_HIGH_HOURS = 14


def envelope_low(instant: datetime) -> date:
    """L(X): the lowest local calendar date X could fall on under any civil
    offset in [-12, +14]."""
    return (instant - timedelta(hours=_ENVELOPE_LOW_HOURS)).date()


def envelope_high(instant: datetime) -> date:
    """H(X): the highest local calendar date X could fall on under any civil
    offset in [-12, +14]."""
    return (instant + timedelta(hours=_ENVELOPE_HIGH_HOURS)).date()


def classify_descriptive(
    *, started_at: datetime, window_days: int, period_start: date, period_end: date
) -> str:
    """DESCRIPTIVE: one observation window ``[A, A + D*24h)``. Returns exactly
    one of QUALIFYING / OUT_OF_WINDOW / EXCLUDED_AMBIGUOUS — total, mutually
    exclusive, exhaustive (frozen Design Freeze §Q)."""
    anchor = started_at
    window_end = anchor + timedelta(hours=24 * window_days)
    low_anchor, high_anchor = envelope_low(anchor), envelope_high(anchor)
    low_end, high_end = envelope_low(window_end), envelope_high(window_end)

    if period_start > high_anchor and period_end < low_end:
        return QUALIFYING
    if period_end < low_anchor or period_start > high_end:
        return OUT_OF_WINDOW
    return EXCLUDED_AMBIGUOUS


def classify_comparative(
    *, started_at: datetime, baseline_days: int, window_days: int, period_start: date, period_end: date
) -> str:
    """COMPARATIVE: a baseline window ``[A - B*24h, A)`` and an observation
    window ``[A, A + D*24h)``. Returns exactly one of QUALIFYING_BASELINE /
    QUALIFYING_OBSERVATION / OUT_OF_WINDOW / EXCLUDED_AMBIGUOUS — total,
    mutually exclusive, exhaustive (frozen Design Freeze §Q). Baseline and
    observation can never both qualify for the same period, since
    ``L(anchor) <= H(anchor)`` always."""
    anchor = started_at
    baseline_start = anchor - timedelta(hours=24 * baseline_days)
    window_end = anchor + timedelta(hours=24 * window_days)

    low_baseline_start, high_baseline_start = envelope_low(baseline_start), envelope_high(baseline_start)
    low_anchor, high_anchor = envelope_low(anchor), envelope_high(anchor)
    low_end, high_end = envelope_low(window_end), envelope_high(window_end)

    if period_start > high_baseline_start and period_end < low_anchor:
        return QUALIFYING_BASELINE
    if period_start > high_anchor and period_end < low_end:
        return QUALIFYING_OBSERVATION
    if period_end < low_baseline_start or period_start > high_end:
        return OUT_OF_WINDOW
    return EXCLUDED_AMBIGUOUS


def periods_overlap(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    """Inclusive-interval overlap (frozen Reconciliation §S/§20): equality on
    either boundary counts as overlap."""
    return a_start <= b_end and b_start <= a_end


def inclusive_period_length(period_start: date, period_end: date) -> int:
    return (period_end - period_start).days + 1
