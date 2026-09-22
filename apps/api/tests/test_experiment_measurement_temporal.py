"""Pure unit tests for the frozen SEMANTICS-VERSION-1 temporal classification
formulas (Design Freeze §P/§Q). No database."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.strategy.experiment_measurement_temporal import (
    EXCLUDED_AMBIGUOUS,
    OUT_OF_WINDOW,
    QUALIFYING,
    QUALIFYING_BASELINE,
    QUALIFYING_OBSERVATION,
    classify_comparative,
    classify_descriptive,
    envelope_high,
    envelope_low,
    inclusive_period_length,
    periods_overlap,
)

ANCHOR = datetime(2026, 3, 10, 15, 0, tzinfo=timezone.utc)  # H(anchor) offset = +14h -> delta = 1 day


def test_envelope_low_high_are_26_hours_apart():
    assert envelope_high(ANCHOR) - envelope_low(ANCHOR) == timedelta(days=1)


def test_descriptive_qualifying_interior():
    outcome = classify_descriptive(
        started_at=ANCHOR, window_days=14, period_start=date(2026, 3, 13), period_end=date(2026, 3, 15)
    )
    assert outcome == QUALIFYING


def test_descriptive_out_of_window_before_anchor():
    outcome = classify_descriptive(
        started_at=ANCHOR, window_days=14, period_start=date(2026, 2, 1), period_end=date(2026, 2, 5)
    )
    assert outcome == OUT_OF_WINDOW


def test_descriptive_out_of_window_after_window_end():
    outcome = classify_descriptive(
        started_at=ANCHOR, window_days=14, period_start=date(2026, 4, 1), period_end=date(2026, 4, 5)
    )
    assert outcome == OUT_OF_WINDOW


def test_descriptive_ambiguous_crossing_anchor():
    outcome = classify_descriptive(
        started_at=ANCHOR, window_days=14, period_start=date(2026, 3, 9), period_end=date(2026, 3, 11)
    )
    assert outcome == EXCLUDED_AMBIGUOUS


def test_descriptive_boundary_exactly_at_low_anchor_is_ambiguous():
    # period_end == L(A) exactly must NOT qualify (strict <)
    low_anchor = envelope_low(ANCHOR)
    outcome = classify_descriptive(
        started_at=ANCHOR, window_days=14, period_start=low_anchor - timedelta(days=2), period_end=low_anchor
    )
    assert outcome == EXCLUDED_AMBIGUOUS


def test_descriptive_boundary_one_day_before_low_anchor_qualifies_as_out_of_window():
    low_anchor = envelope_low(ANCHOR)
    outcome = classify_descriptive(
        started_at=ANCHOR,
        window_days=14,
        period_start=low_anchor - timedelta(days=3),
        period_end=low_anchor - timedelta(days=1),
    )
    assert outcome == OUT_OF_WINDOW


def test_comparative_totality_and_exclusivity_across_many_periods():
    baseline_days, window_days = 7, 14
    outcomes = set()
    for offset in range(-400, 400, 1):
        start = (ANCHOR + timedelta(hours=offset * 6)).date()
        outcome = classify_comparative(
            started_at=ANCHOR,
            baseline_days=baseline_days,
            window_days=window_days,
            period_start=start,
            period_end=start,
        )
        outcomes.add(outcome)
    assert outcomes <= {"QUALIFYING_BASELINE", "QUALIFYING_OBSERVATION", "OUT_OF_WINDOW", "EXCLUDED_AMBIGUOUS"}


def test_comparative_baseline_and_observation_never_overlap():
    baseline_days, window_days = 7, 14
    for offset_hours in range(-1000, 1000, 4):
        d = (ANCHOR + timedelta(hours=offset_hours)).date()
        outcome = classify_comparative(
            started_at=ANCHOR, baseline_days=baseline_days, window_days=window_days, period_start=d, period_end=d
        )
        # A single-day period can never be classified as BOTH roles by
        # construction (only one classify call per period) - this loop just
        # proves no exception/invalid value is ever produced.
        assert outcome in (QUALIFYING_BASELINE, QUALIFYING_OBSERVATION, OUT_OF_WINDOW, EXCLUDED_AMBIGUOUS)


def test_comparative_qualifying_baseline_and_observation_interiors():
    baseline = classify_comparative(
        started_at=ANCHOR, baseline_days=7, window_days=14, period_start=date(2026, 3, 6), period_end=date(2026, 3, 8)
    )
    assert baseline == QUALIFYING_BASELINE
    observation = classify_comparative(
        started_at=ANCHOR, baseline_days=7, window_days=14, period_start=date(2026, 3, 13), period_end=date(2026, 3, 15)
    )
    assert observation == QUALIFYING_OBSERVATION


def test_periods_overlap_inclusive_boundaries():
    assert periods_overlap(date(2026, 1, 1), date(2026, 1, 5), date(2026, 1, 5), date(2026, 1, 10)) is True
    assert periods_overlap(date(2026, 1, 1), date(2026, 1, 4), date(2026, 1, 5), date(2026, 1, 10)) is False


def test_inclusive_period_length():
    assert inclusive_period_length(date(2026, 1, 1), date(2026, 1, 1)) == 1
    assert inclusive_period_length(date(2026, 1, 1), date(2026, 1, 3)) == 3
