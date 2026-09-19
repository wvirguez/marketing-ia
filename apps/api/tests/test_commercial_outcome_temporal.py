"""MVP-36B-R2/R3 — CommercialOutcome ``occurred_at`` temporal range integrity.

The defect (MVP36C-R1-OBS-1): a request-accepted ``occurred_at`` was persisted
successfully but became undecodable by psycopg under a non-UTC PostgreSQL
SESSION timezone, so every later campaign GET and idempotent replay returned
500. The row is a valid ``timestamptz``; the failure happens when psycopg
converts it into the session timezone and the local time leaves Python's
year 1..9999 range. The application never fixes the session timezone, so
correctness cannot rely on UTC.

R3 (MVP36C-R2-OBS-1): the session timezone can also be a numeric/POSIX zone
that PostgreSQL accepts but no IANA database contains — offsets up to
167:59:59, and, through a POSIX DST rule with an implied DST offset,
+168:59:00 — and PostgreSQL renders the local time BEFORE psycopg's UTC
fallback for such zones can matter. The accepted range therefore reserves
eight calendar days at each end, and these tests discover the envelope from
the running PostgreSQL rather than hard-coding it.

These tests therefore make the PostgreSQL session timezone itself
participate: it is set on the application's own connections
(``set_config('TimeZone', ...)`` on every pooled connection) — never
simulated with Python tzinfo objects. All marked `postgres`."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.commercial.models import CommercialOutcome
from app.commercial.schemas import _OCCURRED_AT_MAX, _OCCURRED_AT_MIN
from app.persistence.base import metadata
from app.persistence.session import get_engine
from tests.campaignstest import campaign_payload

pytestmark = pytest.mark.postgres

_PY_MIN = datetime(1, 1, 1, tzinfo=timezone.utc)
_PY_MAX = datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)

MIN_SAFE = "0001-01-09T00:00:00Z"
MAX_SAFE = "9999-12-23T23:59:59.999999Z"

# The largest effective session offsets PostgreSQL 14.24 will accept (see the
# discovery test below, which re-derives them): a POSIX zone with a DST rule
# and no explicit DST offset implies standard + 1h (+167:59 + 1h = +168:59:00),
# and the interval form accepts +/-167:59:59.
WORST_POSITIVE_ZONE = "<+167:59>-167:59DST,M1.1.0/-167,M12.5.0/167"
WORST_POSITIVE_INTERVAL = "interval:+167:59:59"
WORST_NEGATIVE_INTERVAL = "interval:-167:59:59"

# Named zones: the observed failure zone (negative), the most extreme negative
# offset PostgreSQL knows at year 1 (Asia/Manila pre-1844 LMT, -15:56), the
# most positive current one (+14), and UTC as the control. Numeric/POSIX
# zones: exactly 24h (the R2 limit), then beyond it — 25h, 100h — and the
# discovered PostgreSQL maximum in both directions.
SESSION_ZONES = [
    "America/Caracas", "Asia/Manila", "Pacific/Kiritimati", "UTC",
    "<+24>-24", "<-24>+24", "<+25>-25", "<-25>+25", "<+100>-100", "<-100>+100",
    WORST_POSITIVE_ZONE, WORST_POSITIVE_INTERVAL, WORST_NEGATIVE_INTERVAL,
]

# Values the API must reject with 422 BEFORE persistence: the observed poison
# values (lower and upper), the R2 boundaries (now outside the range), the
# first instants outside the current range, and offset-bearing inputs whose UTC
# instant is out of range (these poisoned the campaign even under a UTC
# session before R2).
UNSAFE = [
    "0001-01-01T00:00:00Z",  # observed pre-R2 lower poison
    "0001-01-02T00:00:00Z",  # the R2 lower boundary: insufficient under numeric zones
    "0001-01-08T23:59:59.999999Z",  # 1 microsecond below the lower boundary
    "0001-01-09T00:00:00+14:00",  # UTC instant below the range
    "0001-01-01T00:00:00-14:00",
    "9999-12-24T00:00:00Z",  # first instant above the upper boundary
    "9999-12-24T23:59:59.999999Z",  # the "seven-day" boundary: insufficient (+168:59:00 zone)
    "9999-12-30T23:59:59.999999Z",  # the R2 upper boundary
    "9999-12-31T23:59:59Z",  # observed pre-R2 upper poison
    "9999-12-23T23:59:59.999999-00:01",  # 1 minute past MAX_SAFE by instant: 9999-12-24T00:00:59.999999Z
    "9999-12-31T23:59:59-14:00",  # UTC instant in year 10000
]

# Boundary and ordinary values that must be accepted, readable and replayable.
SAFE = [
    MIN_SAFE,
    MAX_SAFE,
    "0001-01-09T14:00:00+14:00",  # == MIN_SAFE instant, positive-offset spelling
    "9999-12-23T09:59:59.999999-14:00",  # 09:59:59.999999 - (-14:00) => 9999-12-23T23:59:59.999999Z == MAX_SAFE
    "2026-03-01T10:00:00Z",
    "2026-03-01T05:00:00-05:00",
    "2026-03-01T15:30:00+05:30",
]


# --- helpers -----------------------------------------------------------------


@pytest.fixture()
def set_session_timezone(campaign_run_client):
    """Sets the PostgreSQL SESSION timezone on every connection of the
    application's own engine, and undoes it afterwards."""
    engine = get_engine()
    installed: list = []

    def apply(zone: str) -> None:
        for listener in installed:
            event.remove(engine, "connect", listener)
        installed.clear()

        def on_connect(dbapi_connection, _record, _zone=zone):
            cursor = dbapi_connection.cursor()
            if _zone.startswith("interval:"):
                cursor.execute("set time zone interval '%s'" % _zone.split(":", 1)[1])
            else:
                cursor.execute("select set_config('TimeZone', %s, false)", (_zone,))
            cursor.close()
            dbapi_connection.commit()

        event.listen(engine, "connect", on_connect)
        installed.append(on_connect)
        engine.dispose()  # pooled connections were opened under the previous zone
        with engine.connect() as connection:  # prove the zone is really in effect
            if zone.startswith("interval:"):
                sign, hms = (1 if zone.split(":", 1)[1][0] == "+" else -1), zone.split(":", 1)[1][1:]
                hours, minutes, seconds = (int(part) for part in hms.split(":"))
                offset = connection.scalar(text("select extract(timezone from timestamptz '2026-03-01 10:00+00')"))
                assert int(offset) == sign * (hours * 3600 + minutes * 60 + seconds)
            else:
                assert connection.scalar(text("show timezone")) == zone

    yield apply
    for listener in installed:
        event.remove(engine, "connect", listener)
    engine.dispose()


def _path(fixtures: dict, suffix: str = "") -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/commercial-outcomes{suffix}"


def _create(fixtures: dict, **overrides):
    body = {"outcome_type": "sale", "occurred_at": "2026-03-01T10:00:00Z", "client_request_id": str(uuid.uuid4())}
    body.update(overrides)
    return fixtures["client"].post(_path(fixtures), json=body, headers={"X-CSRF-Token": fixtures["csrf_token"]})


def _correct(fixtures: dict, outcome_id: str, **overrides):
    body = {
        "outcome_type": "sale", "occurred_at": "2026-03-01T10:00:00Z",
        "client_request_id": str(uuid.uuid4()), "correction_reason": "Corrected.",
    }
    body.update(overrides)
    return fixtures["client"].post(
        _path(fixtures, f"/{outcome_id}/corrections"), json=body, headers={"X-CSRF-Token": fixtures["csrf_token"]}
    )


def _counts(engine) -> dict[str, int]:
    with Session(engine) as session:
        return {
            name: session.execute(select(func.count()).select_from(table)).scalar_one()
            for name, table in metadata.tables.items()
        }


def _delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {name: after[name] - before[name] for name in after if after[name] != before[name]}


def _persisted_instant_equals(engine, public_id: str, iso: str) -> bool:
    """Compares the stored instant inside PostgreSQL, so the check itself
    never depends on psycopg decoding the value."""
    with Session(engine) as session:
        return bool(
            session.scalar(
                text("select occurred_at = cast(:v as timestamptz) from commercial_outcomes where public_id = :p"),
                {"v": iso, "p": public_id},
            )
        )


def _listing(fixtures: dict):
    return fixtures["client"].get(_path(fixtures))


# --- root cause: the driver, independent of the API --------------------------


def _set_zone(connection, zone: str) -> None:
    """Sets the PostgreSQL session timezone. ``interval:+H:MM:SS`` uses the
    ``SET TIME ZONE INTERVAL`` form; everything else is a zone string."""
    if zone.startswith("interval:"):
        connection.execute(text("set time zone interval '%s'" % zone.split(":", 1)[1]))
    else:
        connection.execute(text("select set_config('TimeZone', :z, false)"), {"z": zone})


def _decode(connection, zone: str, iso: str):
    _set_zone(connection, zone)
    return connection.execute(text("select cast(:v as timestamptz)"), {"v": iso}).scalar_one()


def test_root_cause_out_of_range_local_time_cannot_be_decoded_under_a_session_timezone(postgres_engine) -> None:
    """Documents (and guards) the mechanism that makes the bound necessary:
    a perfectly valid ``timestamptz`` cannot be read back once the session
    timezone moves its local time past Python's year 1 / year 9999 limits —
    at either end. If the driver ever stops failing here, this test says the
    range bound deserves re-evaluation rather than silently going stale."""
    with postgres_engine.connect() as connection:
        with pytest.raises(DBAPIError, match="timestamp too small"):
            _decode(connection, "America/Caracas", "0001-01-01T00:00:00Z")
    with postgres_engine.connect() as connection:
        with pytest.raises(DBAPIError, match="timestamp too large"):
            _decode(connection, "Pacific/Kiritimati", "9999-12-31T23:59:59Z")
    with postgres_engine.connect() as connection:  # control: UTC has no such shift
        assert _decode(connection, "UTC", "0001-01-01T00:00:00Z").year == 1
        assert _decode(connection, "UTC", "9999-12-31T23:59:59.999999Z").year == 9999


def test_safe_range_decodes_under_every_named_postgresql_session_timezone(postgres_engine) -> None:
    """The safe range's justification, proven exhaustively rather than for
    two hand-picked zones: both boundary instants (and the ordinary ones)
    decode under EVERY timezone name the server lists. The sweep is only
    meaningful if it is sensitive, so it also requires that an out-of-range
    control fails under at least one zone at each end."""
    boundaries = [_OCCURRED_AT_MIN.isoformat(), _OCCURRED_AT_MAX.isoformat(), "2026-03-01T10:00:00+00:00"]
    with postgres_engine.connect() as connection:
        zones = [row[0] for row in connection.execute(text("select name from pg_timezone_names order by name"))]
    assert len(zones) > 300  # non-vacuous: the full tz database, not a sample

    failures: list[str] = []
    with postgres_engine.connect() as connection:
        for zone in zones:
            for iso in boundaries:
                try:
                    _decode(connection, zone, iso)
                except DBAPIError as exc:  # pragma: no cover - would be a real defect
                    connection.rollback()
                    failures.append(f"{zone} @ {iso}: {str(exc.orig).splitlines()[0]}")
    assert failures == []

    def fails_somewhere(iso: str) -> bool:
        with postgres_engine.connect() as connection:
            for zone in zones:
                try:
                    _decode(connection, zone, iso)
                except DBAPIError:
                    connection.rollback()
                    return True
        return False

    assert fails_somewhere("0001-01-01T00:00:00Z")  # lower control
    assert fails_somewhere("9999-12-31T23:59:59Z")  # upper control


def test_declared_bounds_are_eight_calendar_days_inside_pythons_range() -> None:
    assert _OCCURRED_AT_MIN == datetime(1, 1, 9, tzinfo=timezone.utc)
    assert _OCCURRED_AT_MAX == datetime(9999, 12, 23, 23, 59, 59, 999999, tzinfo=timezone.utc)
    assert _OCCURRED_AT_MIN - _PY_MIN == timedelta(days=8)
    assert _PY_MAX - _OCCURRED_AT_MAX == timedelta(days=8)


# --- R3: the PostgreSQL numeric / POSIX session-timezone envelope ---------------


def _hms(seconds: int) -> str:
    return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


def _accepts(connection, zone: str) -> bool:
    try:
        _set_zone(connection, zone)
        return True
    except DBAPIError:
        connection.rollback()
        return False


def _largest_accepted(connection, zone_for, step: int, ceiling: int = 1000 * 3600) -> tuple[int, int]:
    """Bisects the largest multiple of ``step`` seconds whose zone PostgreSQL
    accepts, returning ``(largest accepted, first rejected)`` in seconds.
    Acceptance must be monotonic within one specification form — asserted by
    probing sparse larger values — otherwise the bisection would be lying."""
    assert _accepts(connection, zone_for(step))
    low, high = 1, ceiling // step
    assert not _accepts(connection, zone_for(high * step))
    while high - low > 1:
        middle = (low + high) // 2
        if _accepts(connection, zone_for(middle * step)):
            low = middle
        else:
            high = middle
    largest, first_rejected = low * step, high * step
    for probe in (first_rejected + 3600, first_rejected * 2, ceiling):
        assert not _accepts(connection, zone_for(probe)), f"non-monotonic acceptance beyond {_hms(first_rejected)}"
    return largest, first_rejected


def _hm(seconds: int) -> str:
    return "%d:%02d" % (seconds // 3600, seconds % 3600 // 60)


_RANGE_END_INSTANTS = [
    "0001-01-09 00:00:00+00", "0001-03-01 00:00:00+00", "9999-06-30 12:00:00+00",
    "9999-12-23 23:59:59+00", "9999-12-24 12:00:00+00", "9999-12-30 12:00:00+00",
]


def _rendered_offsets(connection, zone: str) -> list[int]:
    _set_zone(connection, zone)
    return [
        int(connection.scalar(text("select extract(timezone from timestamptz '%s')" % instant)))
        for instant in _RANGE_END_INSTANTS
    ]


@pytest.fixture(scope="module")
def envelope(postgres_engine) -> dict:
    """The numeric session-offset envelope of the RUNNING PostgreSQL, measured
    (never assumed): the largest accepted / first rejected offset for each
    specification form, and the largest EFFECTIVE rendered offset — which a
    POSIX DST rule with an implied DST offset pushes one hour past the plain
    limit."""
    found: dict = {}
    with postgres_engine.connect() as connection:
        interval_zone = lambda sign: (lambda seconds: f"interval:{sign}{_hms(seconds)}")
        string_zone = lambda sign: (lambda seconds: f"<{sign}{_hm(seconds)}>{'-' if sign == '+' else '+'}{_hm(seconds)}")
        found["interval_pos"] = _largest_accepted(connection, interval_zone("+"), 1)
        found["interval_neg"] = _largest_accepted(connection, interval_zone("-"), 1)
        found["string_pos"] = _largest_accepted(connection, string_zone("+"), 60)
        found["string_neg"] = _largest_accepted(connection, string_zone("-"), 60)

        std_pos = string_zone("+")(found["string_pos"][0])
        std_neg = string_zone("-")(found["string_neg"][0])
        positive_specs = [interval_zone("+")(found["interval_pos"][0]), std_pos]
        negative_specs = [interval_zone("-")(found["interval_neg"][0]), std_neg]
        for rule in (",M1.1.0/-167,M12.5.0/167", ",J1/0,J365/24"):  # implied DST offset = standard + 1h
            for zone in (std_pos + "DST" + rule, std_neg + "DST" + rule):
                assert _accepts(connection, zone), f"PostgreSQL no longer accepts the implied-DST zone {zone}"
                offsets = _rendered_offsets(connection, zone)
                (positive_specs if max(offsets) > 0 else negative_specs).append(zone)
        found["effective_positive"] = max(max(_rendered_offsets(connection, z)) for z in positive_specs)
        found["effective_negative"] = -min(min(_rendered_offsets(connection, z)) for z in negative_specs)
    return found


def test_postgresql_numeric_session_offset_envelope_is_reached_and_the_range_covers_it(envelope) -> None:
    """Sensitivity: every specification form's limit is genuinely REACHED —
    the largest accepted offset is accepted, the very next step is rejected,
    and larger values stay rejected — so the margin below is proven against
    the PostgreSQL boundary itself, not against an arbitrarily large number."""
    for form in ("interval_pos", "interval_neg", "string_pos", "string_neg"):
        largest, first_rejected = envelope[form]
        assert first_rejected > largest > 0, form
    # The plain limit is 168h: the last accepted step is just below it.
    assert envelope["interval_pos"][1] == envelope["interval_neg"][1] == 168 * 3600
    assert envelope["string_pos"][1] == envelope["string_neg"][1] == 168 * 3600
    # The effective offset can exceed the plain limit (implied DST hour); it is never smaller.
    assert envelope["effective_positive"] >= envelope["interval_pos"][0]
    assert envelope["effective_negative"] >= envelope["interval_neg"][0]
    # The declared range reserves at least the full effective envelope at each end.
    assert _OCCURRED_AT_MIN - _PY_MIN >= timedelta(seconds=envelope["effective_negative"])
    assert _PY_MAX - _OCCURRED_AT_MAX >= timedelta(seconds=envelope["effective_positive"])


def test_range_margin_is_tight_against_the_envelope_and_the_earlier_margins_were_not_enough(
    postgres_engine, envelope
) -> None:
    """Proves the discovered envelope is real and the range not merely large:
    at the exact minimal bound derived from it the worst-case zone still
    decodes, ONE SECOND beyond it fails — and the R2 boundaries and the
    'seven calendar days' guess fail under PostgreSQL-accepted zones."""
    exact_min = _PY_MIN + timedelta(seconds=envelope["effective_negative"])
    exact_max = _PY_MAX - timedelta(seconds=envelope["effective_positive"])
    assert _OCCURRED_AT_MIN >= exact_min and _OCCURRED_AT_MAX <= exact_max
    one_second = timedelta(seconds=1)
    iso = lambda value: value.isoformat()
    with postgres_engine.connect() as connection:
        assert _decode(connection, WORST_POSITIVE_ZONE, iso(exact_max)).year == 9999
        assert _decode(connection, WORST_NEGATIVE_INTERVAL, iso(exact_min)).year == 1
    for zone, beyond, message in (
        (WORST_POSITIVE_ZONE, exact_max + one_second, "timestamp too large"),
        (WORST_NEGATIVE_INTERVAL, exact_min - one_second, "timestamp too small"),
        (WORST_POSITIVE_ZONE, datetime(9999, 12, 30, 23, 59, 59, 999999, tzinfo=timezone.utc), "timestamp too large"),  # R2 MAX
        (WORST_POSITIVE_ZONE, datetime(9999, 12, 24, 23, 59, 59, 999999, tzinfo=timezone.utc), "timestamp too large"),  # 7 days
        (WORST_NEGATIVE_INTERVAL, datetime(1, 1, 2, tzinfo=timezone.utc), "timestamp too small"),  # R2 MIN
    ):
        with postgres_engine.connect() as connection:
            with pytest.raises(DBAPIError, match=message):
                _decode(connection, zone, iso(beyond))


@pytest.mark.parametrize(
    "zone",
    [
        "<+24>-24", "<-24>+24", "<+25>-25", "<-25>+25", "<+100>-100", "<-100>+100",
        "<+167>-167", "<-167>+167", WORST_POSITIVE_ZONE, WORST_POSITIVE_INTERVAL, WORST_NEGATIVE_INTERVAL,
    ],
)
def test_bounds_and_ordinary_instants_decode_under_numeric_and_posix_session_zones(postgres_engine, zone) -> None:
    with postgres_engine.connect() as connection:
        for iso in (_OCCURRED_AT_MIN.isoformat(), _OCCURRED_AT_MAX.isoformat(), "2026-03-01T10:00:00+00:00"):
            assert _decode(connection, zone, iso).tzinfo is not None, (zone, iso)


@pytest.mark.parametrize(
    "zone, psycopg_falls_back_to_utc",
    [
        ("EST5EDT", False),  # named POSIX-style zone psycopg recognises
        ("posix/Asia/Kolkata", False),
        ("+05:30", True),  # numeric string: PostgreSQL renders it, psycopg does not recognise it
        ("<+14>-14", True),  # angle-bracket POSIX form
        ("UTC+5", True),
        (WORST_POSITIVE_ZONE, True),
        (WORST_POSITIVE_INTERVAL, True),
        (WORST_NEGATIVE_INTERVAL, True),
    ],
)
def test_temporal_safety_does_not_depend_on_psycopg_recognising_the_session_timezone(
    postgres_engine, zone, psycopg_falls_back_to_utc
) -> None:
    """The fallback is observed through the decoded tzinfo (psycopg logs its
    warning only once per zone name, which would make a log assertion depend
    on test order). Whether or not psycopg recognises the zone, both bounds
    decode: the safety comes from the UTC margin, not from the fallback."""
    with postgres_engine.connect() as connection:
        for iso in (_OCCURRED_AT_MIN.isoformat(), _OCCURRED_AT_MAX.isoformat(), "2026-03-01T10:00:00+00:00"):
            value = _decode(connection, zone, iso)
            assert (value.tzinfo == timezone.utc) is psycopg_falls_back_to_utc, (zone, iso, value.tzinfo)


# --- API: create --------------------------------------------------------------


@pytest.mark.parametrize("zone", SESSION_ZONES)
def test_unsafe_create_is_rejected_422_before_persistence_and_the_campaign_stays_readable(
    campaign_run_client, postgres_engine, set_session_timezone, zone
) -> None:
    fixtures = campaign_run_client
    set_session_timezone(zone)
    for value in UNSAFE:
        before = _counts(postgres_engine)
        response = _create(fixtures, occurred_at=value)
        assert response.status_code == 422, (zone, value, response.status_code, response.text[:120])
        assert _delta(before, _counts(postgres_engine)) == {}, (zone, value)  # no row, no audit, no other table
        listing = _listing(fixtures)
        assert listing.status_code == 200 and listing.json() == [], (zone, value)  # never poisoned


@pytest.mark.parametrize("zone", SESSION_ZONES)
def test_safe_create_is_accepted_readable_and_replayable_under_the_session_timezone(
    campaign_run_client, postgres_engine, set_session_timezone, zone
) -> None:
    fixtures = campaign_run_client
    set_session_timezone(zone)
    for value in SAFE:
        key = str(uuid.uuid4())
        before = _counts(postgres_engine)
        created = _create(fixtures, client_request_id=key, occurred_at=value)
        assert created.status_code == 201, (zone, value, created.text[:120])
        public_id = created.json()["id"]
        assert _delta(before, _counts(postgres_engine)) == {"commercial_outcomes": 1, "audit_events": 1}, (zone, value)
        assert _persisted_instant_equals(postgres_engine, public_id, value), (zone, value)

        listing = _listing(fixtures)
        assert listing.status_code == 200, (zone, value, listing.status_code)
        assert public_id in [row["id"] for row in listing.json()], (zone, value)

        after_create = _counts(postgres_engine)
        replay = _create(fixtures, client_request_id=key, occurred_at=value)
        assert replay.status_code == 200 and replay.json()["id"] == public_id, (zone, value, replay.status_code)
        assert _delta(after_create, _counts(postgres_engine)) == {}, (zone, value)  # replay writes nothing


@pytest.mark.parametrize("zone", SESSION_ZONES)
def test_equivalent_spellings_of_the_boundary_and_ordinary_instants_replay(
    campaign_run_client, postgres_engine, set_session_timezone, zone
) -> None:
    """D4 preserved: the same instant spelled with another offset is the same
    request (200, same row, one audit) — including at the safe boundaries."""
    fixtures = campaign_run_client
    set_session_timezone(zone)
    families = [
        ["0001-01-09T00:00:00Z", "0001-01-09T14:00:00+14:00", "0001-01-08T12:00:00-12:00"],
        ["9999-12-23T23:59:59.999999Z", "9999-12-23T09:59:59.999999-14:00", "9999-12-24T13:59:59.999999+14:00"],
        ["2026-03-01T10:00:00Z", "2026-03-01T05:00:00-05:00", "2026-03-01T15:30:00+05:30", "2026-03-01T10:00:00.000000Z"],
    ]
    for family in families:
        key = str(uuid.uuid4())
        first = _create(fixtures, client_request_id=key, occurred_at=family[0])
        assert first.status_code == 201, (zone, family[0], first.text[:120])
        after_first = _counts(postgres_engine)
        for spelling in family[1:]:
            replay = _create(fixtures, client_request_id=key, occurred_at=spelling)
            assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"], (zone, spelling, replay.status_code)
        assert _delta(after_first, _counts(postgres_engine)) == {}, (zone, family[0])
        with Session(postgres_engine) as session:
            outcome_ids = list(
                session.scalars(select(CommercialOutcome.id).where(CommercialOutcome.client_request_id == key))
            )
            audits = session.scalar(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.commercial_outcome_id.in_(outcome_ids))
            )
        assert (len(outcome_ids), audits) == (1, 1), (zone, family[0])
        different = _create(fixtures, client_request_id=key, occurred_at="2026-03-01T10:00:01Z")
        assert different.status_code == 409  # a genuinely different instant is still a conflict


# --- API: correction ----------------------------------------------------------


@pytest.mark.parametrize("zone", SESSION_ZONES)
def test_unsafe_correction_is_rejected_422_original_stays_current_and_nothing_is_written(
    campaign_run_client, postgres_engine, set_session_timezone, zone
) -> None:
    fixtures = campaign_run_client
    set_session_timezone(zone)
    original = _create(fixtures).json()
    for value in UNSAFE:
        before = _counts(postgres_engine)
        response = _correct(fixtures, original["id"], occurred_at=value)
        assert response.status_code == 422, (zone, value, response.status_code, response.text[:120])
        assert _delta(before, _counts(postgres_engine)) == {}, (zone, value)  # no successor, no corrected audit
        listing = _listing(fixtures)
        assert listing.status_code == 200, (zone, value)
        assert [(row["id"], row["is_current"], row["corrected_by_commercial_outcome_id"]) for row in listing.json()] == [
            (original["id"], True, None)
        ], (zone, value)


@pytest.mark.parametrize("zone", SESSION_ZONES)
def test_safe_correction_is_accepted_readable_and_replayable_under_the_session_timezone(
    campaign_run_client, postgres_engine, set_session_timezone, zone
) -> None:
    fixtures = campaign_run_client
    set_session_timezone(zone)
    tip = _create(fixtures).json()["id"]
    for value in (MIN_SAFE, MAX_SAFE, "0001-01-09T14:00:00+14:00", "2026-03-01T05:00:00-05:00"):
        key = str(uuid.uuid4())
        before = _counts(postgres_engine)
        corrected = _correct(fixtures, tip, client_request_id=key, occurred_at=value)
        assert corrected.status_code == 201, (zone, value, corrected.text[:120])
        new_id = corrected.json()["id"]
        assert _delta(before, _counts(postgres_engine)) == {"commercial_outcomes": 1, "audit_events": 1}, (zone, value)
        assert _persisted_instant_equals(postgres_engine, new_id, value), (zone, value)
        listing = _listing(fixtures)
        assert listing.status_code == 200, (zone, value, listing.status_code)
        assert {row["id"]: row["is_current"] for row in listing.json()}[new_id] is True, (zone, value)

        after = _counts(postgres_engine)
        replay = _correct(fixtures, tip, client_request_id=key, occurred_at=value)  # the target is now stale
        assert replay.status_code == 200 and replay.json()["id"] == new_id, (zone, value, replay.status_code)
        assert _delta(after, _counts(postgres_engine)) == {}, (zone, value)
        tip = new_id


# --- epoch-number inputs (MVP36C-R2-OBS-4 is deliberately NOT changed) ---------

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
# Exact integer timedelta division (``total_seconds()`` is a float and rounds
# ...59.999999 up at this magnitude): the first and the last whole millisecond
# that are inside the range.
_MIN_MS = (_OCCURRED_AT_MIN - _EPOCH) // timedelta(milliseconds=1)
_MAX_MS = (_OCCURRED_AT_MAX - _EPOCH) // timedelta(milliseconds=1)

EPOCH_INPUTS = [
    ("ordinary integer", 1772359200),
    ("ordinary float", 1772359200.5),
    ("very negative", -1e20),
    ("very positive", 1e20),
    ("nan", "nan"),
    ("inf", "inf"),
    ("lower boundary, milliseconds", _MIN_MS),
    ("one millisecond below the lower boundary", _MIN_MS - 1),
    ("upper boundary, milliseconds", _MAX_MS),
    ("one millisecond above the upper boundary", _MAX_MS + 1),
]


@pytest.mark.parametrize("zone", ["UTC", WORST_POSITIVE_ZONE, WORST_NEGATIVE_INTERVAL])
def test_epoch_number_inputs_never_500_and_never_persist_an_unsafe_instant(
    campaign_run_client, postgres_engine, set_session_timezone, zone
) -> None:
    """Pydantic also accepts epoch numbers for an aware datetime (numbers
    above 2e10 in absolute value are read as milliseconds — an interpretation
    this repair does not change). Whatever an epoch number turns into, the
    range validator still sees the resulting instant: it is either rejected
    with 422 and zero mutation, or persisted inside the range and readable."""
    fixtures = campaign_run_client
    set_session_timezone(zone)
    original = _create(fixtures).json()["id"]
    inside = f"select occurred_at >= cast('{_OCCURRED_AT_MIN.isoformat()}' as timestamptz) and occurred_at <= cast('{_OCCURRED_AT_MAX.isoformat()}' as timestamptz) from commercial_outcomes where public_id = :p"
    outcomes = {}
    for label, value in EPOCH_INPUTS:
        for kind in ("create", "correction"):
            before = _counts(postgres_engine)
            response = _create(fixtures, occurred_at=value) if kind == "create" else _correct(fixtures, original, occurred_at=value)
            assert response.status_code in (201, 422), (zone, label, kind, response.status_code, response.text[:120])
            if response.status_code == 422:
                assert _delta(before, _counts(postgres_engine)) == {}, (zone, label, kind)
            else:
                with Session(postgres_engine) as session:
                    assert session.scalar(text(inside), {"p": response.json()["id"]}) is True, (zone, label, kind)
                if kind == "correction":
                    original = response.json()["id"]  # the new tip, so the next correction targets a current row
            outcomes[(label, kind)] = response.status_code
            assert _listing(fixtures).status_code == 200, (zone, label, kind)  # never poisoned
    # The two boundary milliseconds: inside is accepted, one millisecond outside is not.
    for kind in ("create", "correction"):
        assert outcomes[("lower boundary, milliseconds", kind)] == 201
        assert outcomes[("one millisecond below the lower boundary", kind)] == 422
        assert outcomes[("upper boundary, milliseconds", kind)] == 201
        assert outcomes[("one millisecond above the upper boundary", kind)] == 422
        for label in ("very negative", "very positive", "nan", "inf"):
            assert outcomes[(label, kind)] == 422, (label, kind)


# --- downstream non-effects under a non-UTC session ---------------------------


@pytest.mark.parametrize("zone", ["America/Caracas", "Pacific/Kiritimati", WORST_POSITIVE_ZONE, WORST_NEGATIVE_INTERVAL])
def test_one_create_and_one_correction_only_touch_outcomes_and_audit_under_a_non_utc_session(
    campaign_run_client, postgres_engine, set_session_timezone, zone
) -> None:
    fixtures = campaign_run_client
    set_session_timezone(zone)
    before = _counts(postgres_engine)
    created = _create(fixtures, occurred_at=MIN_SAFE)
    corrected = _correct(fixtures, created.json()["id"], occurred_at=MAX_SAFE)
    assert (created.status_code, corrected.status_code) == (201, 201)
    assert _delta(before, _counts(postgres_engine)) == {"commercial_outcomes": 2, "audit_events": 2}
    assert _listing(fixtures).status_code == 200


def test_creating_a_second_campaign_in_the_same_session_is_unaffected(campaign_run_client, set_session_timezone) -> None:
    """Guards the fixture itself: the timezone override does not break the
    ordinary campaign flow the tests above depend on."""
    fixtures = campaign_run_client
    set_session_timezone("Pacific/Kiritimati")
    response = fixtures["client"].post(
        "/api/v1/campaigns", json=campaign_payload(), headers={"X-CSRF-Token": fixtures["csrf_token"]}
    )
    assert response.status_code == 201
