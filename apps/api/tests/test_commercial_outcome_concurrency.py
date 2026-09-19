"""Real PostgreSQL concurrency proofs for CommercialOutcome create/
correction idempotency and the TIP-ONLY correction-target race (MVP-36,
frozen by MVP-36A/-R1). Mirrors ``tests/test_commercial_concurrency.py``'s
own ``_run_two``/``pg_blocking_pids()`` techniques exactly — the second
connection must be observed genuinely blocked by PostgreSQL before the
first operation runs where a lock-based race is being proven. No
sleep-based ordering, no random winner left to chance where determinism
is achievable."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from threading import Event
from time import monotonic, sleep

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.campaigns.repository import CampaignRepository
from app.commercial.models import CommercialOutcome
from app.commercial.service import EVENT_OUTCOME_RECORDED, CommercialService
from app.core.api_errors import ApiError
from tests.commercialtest import build_campaign, build_commercial_outcome
from tests.contenttest import make_user

pytestmark = pytest.mark.postgres

_OCCURRED_AT = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def _run_two(engine, campaign_public_id, operation):
    """Runs ``operation(session, which, campaign)`` on two independent
    PostgreSQL sessions. MVP-36B-R1: each worker loads its own Campaign row
    and records its own ``pg_backend_pid()`` BEFORE a ``threading.Barrier``,
    and both workers are released from that Barrier together, so the two
    competing service calls start at the same instant rather than whenever
    the thread pool happens to schedule them. The Barrier narrows the race
    but cannot by itself prove the two inserts overlapped — the
    ``test_forced_overlap_*`` tests below prove that deterministically via
    ``pg_blocking_pids()``."""
    results: list[tuple] = []
    errors: list[Exception] = []
    backend_pids: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=10)

    def worker(which: int) -> None:
        with Session(engine, expire_on_commit=False) as session:
            pid = session.scalar(text("select pg_backend_pid()"))
            campaign_row = CampaignRepository(session).get_by_public_id(campaign_public_id)
            with lock:
                backend_pids.append(pid)
            barrier.wait()
            try:
                outcome, created = operation(session, which, campaign_row)
                with lock:
                    results.append(("ok", outcome.public_id, created))
            except ApiError as exc:
                session.rollback()
                with lock:
                    results.append(("conflict" if exc.status_code == 409 else f"error:{exc.status_code}", exc.code, None))
            except Exception as exc:  # pragma: no cover - unexpected
                session.rollback()
                with lock:
                    errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, 0), pool.submit(worker, 1)]
        for future in futures:
            future.result(timeout=20)
    return results, errors, backend_pids


def _wait_for_genuine_block(postgres_engine, *, waiting_pid: int, holding_pid: int) -> None:
    deadline = monotonic() + 10
    with postgres_engine.connect() as observer:
        while monotonic() < deadline:
            blockers = observer.execute(text("select pg_blocking_pids(:pid)"), {"pid": waiting_pid}).scalar_one()
            observer.commit()
            if holding_pid in blockers:
                return
            sleep(0.01)
    raise AssertionError("Second operation never demonstrated a PostgreSQL lock wait")


# --- create idempotency concurrency -------------------------------------------

_ITERATIONS = 5  # Barrier-synchronized rounds per scenario (MVP-36B-R1: >= 3)
_FORCED_ITERATIONS = 3  # forced-overlap rounds per scenario (MVP-36B-R1: >= 3)


def _setup_campaign(engine):
    with Session(engine) as setup:
        campaign = build_campaign(setup)
        setup.commit()
        campaign_public_id = campaign.public_id
        user_id = make_user(setup).id
        setup.commit()
    return campaign_public_id, user_id


def _record(
    session: Session, campaign, user_id, *, key: str, outcome_type: str = "purchase",
    monetary_value=Decimal("50.00"), currency="USD", service: CommercialService | None = None,
):
    return (service or CommercialService(session)).record_commercial_outcome(
        campaign=campaign, content_distribution_id=None, outcome_type=outcome_type,
        quantity=None, monetary_value=monetary_value, currency=currency, occurred_at=_OCCURRED_AT,
        external_reference=None, client_request_id=key, actor_user_id=user_id,
    )


def _persisted_rows_and_audits(engine, *, key: str | None = None, public_ids: list[str] | None = None):
    """(CommercialOutcome rows, recorded AuditEvents pointing at those rows)."""
    with Session(engine) as check:
        query = select(CommercialOutcome)
        if key is not None:
            query = query.where(CommercialOutcome.client_request_id == key)
        if public_ids is not None:
            query = query.where(CommercialOutcome.public_id.in_(public_ids))
        rows = list(check.scalars(query))
        ids = [row.id for row in rows]
        audits = list(
            check.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type == EVENT_OUTCOME_RECORDED, AuditEvent.commercial_outcome_id.in_(ids)
                )
            )
        )
        return rows, audits


def test_two_concurrent_creates_same_key_same_payload_exactly_one_row(postgres_engine) -> None:
    """C1: same key + same payload. Exactly one row, exactly one recorded
    AuditEvent, the same public id on both sides, one created=True and one
    created=False — repeated over ``_ITERATIONS`` Barrier-synchronized rounds."""
    campaign_public_id, user_id = _setup_campaign(postgres_engine)
    for _ in range(_ITERATIONS):
        key = str(uuid.uuid4())
        results, errors, backend_pids = _run_two(
            postgres_engine, campaign_public_id, lambda session, which, campaign: _record(session, campaign, user_id, key=key)
        )
        assert not errors
        assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
        assert [r[0] for r in results] == ["ok", "ok"]  # 201 winner + 200 replay — the service never raises for a matching replay
        assert results[0][1] == results[1][1]  # same public id
        assert sorted(r[2] for r in results) == [False, True]  # one created, one replay

        rows, audits = _persisted_rows_and_audits(postgres_engine, key=key)
        assert len(rows) == 1
        assert rows[0].public_id == results[0][1]
        assert len(audits) == 1


def test_two_concurrent_creates_same_key_different_payload_one_conflict(postgres_engine) -> None:
    """C2: same key + materially different payload. Exactly one row, exactly
    one recorded AuditEvent, exactly one success and one
    IDEMPOTENCY_KEY_CONFLICT — never two successes (a duplicate row), never
    two conflicts (one payload must be first)."""
    campaign_public_id, user_id = _setup_campaign(postgres_engine)
    for _ in range(_ITERATIONS):
        key = str(uuid.uuid4())
        results, errors, backend_pids = _run_two(
            postgres_engine,
            campaign_public_id,
            lambda session, which, campaign: _record(
                session, campaign, user_id, key=key, outcome_type="purchase" if which == 0 else "lead",
                monetary_value=None, currency=None,
            ),
        )
        assert not errors
        assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
        assert sorted(r[0] for r in results) == ["conflict", "ok"]
        winner = next(r for r in results if r[0] == "ok")
        loser = next(r for r in results if r[0] == "conflict")
        assert winner[2] is True  # the success is a genuine create
        assert loser[1] == "IDEMPOTENCY_KEY_CONFLICT"

        rows, audits = _persisted_rows_and_audits(postgres_engine, key=key)
        assert len(rows) == 1
        assert rows[0].public_id == winner[1]
        assert len(audits) == 1


def test_two_concurrent_creates_different_keys_identical_fields_both_succeed(postgres_engine) -> None:
    """C3: different keys + identical business fields are two genuine,
    independent events — two rows, two recorded AuditEvents, both
    created=True, distinct public ids."""
    campaign_public_id, user_id = _setup_campaign(postgres_engine)
    for _ in range(_ITERATIONS):
        keys = [str(uuid.uuid4()), str(uuid.uuid4())]
        results, errors, backend_pids = _run_two(
            postgres_engine,
            campaign_public_id,
            lambda session, which, campaign: _record(
                session, campaign, user_id, key=keys[which], outcome_type="lead", monetary_value=None, currency=None
            ),
        )
        assert not errors
        assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
        assert [r[0] for r in results] == ["ok", "ok"]
        assert [r[2] for r in results] == [True, True]
        public_ids = [r[1] for r in results]
        assert public_ids[0] != public_ids[1]

        rows, audits = _persisted_rows_and_audits(postgres_engine, public_ids=public_ids)
        assert len(rows) == 2
        assert len(audits) == 2
        assert {audit.commercial_outcome_id for audit in audits} == {row.id for row in rows}


def _forced_overlap_create(engine, campaign_public_id, user_id, *, key: str, payload_a: dict, payload_b: dict):
    """Deterministic overlap of two REAL ``record_commercial_outcome`` calls
    on the same key, independent of scheduler luck.

    Session A runs the real service: its outcome INSERT executes (holding
    the row and the UNIQUE(workspace_id, client_request_id) entry
    uncommitted), then its audit-record call PAUSES on an Event. Session B
    then runs the real service for the same key; it cannot see A's
    uncommitted row, so it proceeds to its own INSERT and blocks on A's
    unique-index entry. The test proves that block via PostgreSQL's own
    ``pg_blocking_pids()`` — only then is A released to write its
    AuditEvent and commit. Returns ``(result_a, result_b, pid_a, pid_b)``."""
    a_inserted = Event()
    b_ready = Event()
    release_a = Event()
    pids: dict[str, int] = {}
    results: dict[str, tuple] = {}

    def attempt(session: Session, label: str, payload: dict) -> None:
        campaign_row = CampaignRepository(session).get_by_public_id(campaign_public_id)
        try:
            outcome, created = _record(session, campaign_row, user_id, key=key, **payload)
            results[label] = ("ok", outcome.public_id, created)
        except ApiError as exc:
            session.rollback()
            results[label] = ("conflict" if exc.status_code == 409 else f"error:{exc.status_code}", exc.code, None)

    def run_a() -> None:
        with Session(engine, expire_on_commit=False) as session:
            pids["a"] = session.scalar(text("select pg_backend_pid()"))
            service = CommercialService(session)
            original_record = service.events.record

            def paused_record(*args, **kwargs):
                a_inserted.set()  # A's outcome INSERT has already executed
                assert release_a.wait(20), "forced-overlap release never arrived"
                return original_record(*args, **kwargs)

            service.events.record = paused_record
            campaign_row = CampaignRepository(session).get_by_public_id(campaign_public_id)
            outcome, created = _record(session, campaign_row, user_id, key=key, service=service, **payload_a)
            results["a"] = ("ok", outcome.public_id, created)

    def run_b() -> None:
        assert a_inserted.wait(20)
        with Session(engine, expire_on_commit=False) as session:
            pids["b"] = session.scalar(text("select pg_backend_pid()"))
            b_ready.set()
            attempt(session, "b", payload_b)

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(run_a)
        future_b = pool.submit(run_b)
        try:
            assert b_ready.wait(20)
            assert pids["a"] != pids["b"]
            _wait_for_genuine_block(engine, waiting_pid=pids["b"], holding_pid=pids["a"])
        finally:
            release_a.set()
        future_a.result(timeout=20)
        future_b.result(timeout=20)
    return results["a"], results["b"], pids["a"], pids["b"]


def test_forced_overlap_same_key_same_payload_replays_the_winner(postgres_engine) -> None:
    campaign_public_id, user_id = _setup_campaign(postgres_engine)
    payload = {"outcome_type": "purchase", "monetary_value": Decimal("50.00"), "currency": "USD"}
    for _ in range(_FORCED_ITERATIONS):
        key = str(uuid.uuid4())
        result_a, result_b, pid_a, pid_b = _forced_overlap_create(
            postgres_engine, campaign_public_id, user_id, key=key, payload_a=payload, payload_b=payload
        )
        assert pid_a != pid_b
        assert result_a[0] == "ok" and result_a[2] is True  # A held the row first: the genuine create
        assert result_b[0] == "ok" and result_b[2] is False  # B, blocked on A's key, resolves to a replay
        assert result_b[1] == result_a[1]  # same public id

        rows, audits = _persisted_rows_and_audits(postgres_engine, key=key)
        assert len(rows) == 1
        assert rows[0].public_id == result_a[1]
        assert len(audits) == 1


def test_forced_overlap_same_key_different_payload_conflicts(postgres_engine) -> None:
    campaign_public_id, user_id = _setup_campaign(postgres_engine)
    payload_a = {"outcome_type": "purchase", "monetary_value": None, "currency": None}
    payload_b = {"outcome_type": "lead", "monetary_value": None, "currency": None}
    for _ in range(_FORCED_ITERATIONS):
        key = str(uuid.uuid4())
        result_a, result_b, pid_a, pid_b = _forced_overlap_create(
            postgres_engine, campaign_public_id, user_id, key=key, payload_a=payload_a, payload_b=payload_b
        )
        assert pid_a != pid_b
        assert result_a[0] == "ok" and result_a[2] is True
        assert result_b[:2] == ("conflict", "IDEMPOTENCY_KEY_CONFLICT")

        rows, audits = _persisted_rows_and_audits(postgres_engine, key=key)
        assert len(rows) == 1
        assert rows[0].public_id == result_a[1]
        assert rows[0].outcome_type == "purchase"  # the loser never overwrote the winner
        assert len(audits) == 1


# --- correction TIP-ONLY concurrency (forced ordering) ------------------------


def test_forced_ordering_two_corrections_of_the_same_effective_outcome(postgres_engine) -> None:
    """Both concurrent corrections target the SAME effective (tip)
    Outcome — the one genuine TIP-ONLY race. Exactly one succeeds, the
    other deterministically 409s COMMERCIAL_OUTCOME_CORRECTION_TARGET_STALE,
    regardless of which one is released first — mirrors
    ``test_forced_ordering_two_supersede_attempts_on_the_same_objective``
    exactly, substituting CommercialOutcome's own ``for_update`` lock on
    the correction target."""
    with Session(postgres_engine) as setup:
        user = make_user(setup)
        campaign, original = build_commercial_outcome(setup, actor_user_id=user.id)
        setup.commit()
        campaign_public_id = campaign.public_id
        outcome_public_id = original.public_id
        original_id = original.id
        user_id = user.id

    ready = Event()
    second_pid: list[int] = []
    with Session(postgres_engine, expire_on_commit=False) as first, ThreadPoolExecutor(max_workers=1) as pool:
        first.scalar(select(CommercialOutcome).where(CommercialOutcome.id == original_id).with_for_update())
        first_pid = first.scalar(text("select pg_backend_pid()"))

        def second() -> str:
            with Session(postgres_engine, expire_on_commit=False) as s:
                second_pid.append(s.scalar(text("select pg_backend_pid()")))
                ready.set()
                campaign_row = CampaignRepository(s).get_by_public_id(campaign_public_id)
                try:
                    CommercialService(s).correct_commercial_outcome(
                        campaign=campaign_row, target_outcome_public_id=outcome_public_id,
                        outcome_type="second correction", quantity=None, monetary_value=None, currency=None,
                        occurred_at=_OCCURRED_AT, external_reference=None,
                        client_request_id=str(uuid.uuid4()), correction_reason="Second.", actor_user_id=user_id,
                    )
                    return "ok"
                except ApiError as exc:
                    s.rollback()
                    assert exc.status_code == 409
                    assert exc.code == "COMMERCIAL_OUTCOME_CORRECTION_TARGET_STALE"
                    return "conflict"

        task = pool.submit(second)
        try:
            assert ready.wait(10)
            assert second_pid[0] != first_pid
            _wait_for_genuine_block(postgres_engine, waiting_pid=second_pid[0], holding_pid=first_pid)

            campaign_row = CampaignRepository(first).get_by_public_id(campaign_public_id)
            try:
                CommercialService(first).correct_commercial_outcome(
                    campaign=campaign_row, target_outcome_public_id=outcome_public_id,
                    outcome_type="first correction", quantity=None, monetary_value=None, currency=None,
                    occurred_at=_OCCURRED_AT, external_reference=None,
                    client_request_id=str(uuid.uuid4()), correction_reason="First.", actor_user_id=user_id,
                )
                first_result = "ok"
            except ApiError as exc:
                first.rollback()
                assert exc.status_code == 409
                first_result = "conflict"
        finally:
            first.rollback()
        second_result = task.result(timeout=15)

    assert {first_result, second_result} == {"ok", "conflict"}

    with Session(postgres_engine) as check:
        rows = list(
            check.scalars(select(CommercialOutcome).where(CommercialOutcome.campaign_id == campaign.id))
        )
        assert len(rows) == 2  # original + exactly one successor, never a branch
        original_row = next(r for r in rows if r.id == original_id)
        successor_row = next(r for r in rows if r.id != original_id)
        assert successor_row.supersedes_outcome_id == original_id
        events = check.execute(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.commercial_outcome_id == successor_row.id)
        ).scalar_one()
        assert events == 1  # exactly one correction AuditEvent — winner only
