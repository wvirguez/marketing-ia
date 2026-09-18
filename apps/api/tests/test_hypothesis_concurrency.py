"""Forced real PostgreSQL lock orderings for Governed Next-Cycle Hypothesis
creation (MVP-31B, frozen MVP-31A/-31A-R1 contract), mirroring
``tests/test_strategy_revision_concurrency.py``'s own
``pg_blocking_pids()``-based technique exactly — the second connection
must be observed genuinely blocked by PostgreSQL before the first
operation runs. No sleep-based order, no random winner.

Hypothesis-create only ever locks the Strategy row itself (never a
Decision row), so it cannot deadlock against StrategyRevision's own
Decision-then-Strategy lock order (MVP-31A §28/§29) — both orderings below
confirm the expected, non-cyclic outcome.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.campaigns.repository import CampaignRepository
from app.core.api_errors import ApiError
from app.orchestration.service import StrategyRevisionService
from app.strategy.models import Hypothesis, Strategy
from app.strategy.service import StrategyService
from tests.orchestrationtest import build_base_strategy, build_strategic_approval

pytestmark = pytest.mark.postgres


def _run_two(engine, operation):
    """Mirrors ``tests.test_strategy_revision_concurrency._run_two`` exactly."""
    outcomes: list[str] = []
    errors: list[Exception] = []
    backend_pids: list[int] = []
    lock = threading.Lock()

    def worker(which: int) -> None:
        with Session(engine, expire_on_commit=False) as session:
            pid = session.scalar(text("select pg_backend_pid()"))
            with lock:
                backend_pids.append(pid)
            try:
                operation(session, which)
                with lock:
                    outcomes.append("ok")
            except ApiError as exc:
                session.rollback()
                with lock:
                    outcomes.append("conflict" if exc.status_code == 409 else f"error:{exc.status_code}")
            except Exception as exc:  # pragma: no cover - unexpected
                session.rollback()
                with lock:
                    errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, 0), pool.submit(worker, 1)]
        for future in futures:
            future.result(timeout=15)
    return outcomes, errors, backend_pids


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


# --- Race A: two concurrent creates under the same Strategy -------------------


def test_two_concurrent_creates_under_the_same_strategy_both_succeed(postgres_engine) -> None:
    with Session(postgres_engine) as setup:
        campaign, _recommendation, _decision, _approval, actor = build_strategic_approval(setup)
        base_strategy, _run, _stage = build_base_strategy(setup, campaign=campaign)
        setup.commit()
        campaign_public_id = campaign.public_id
        base_strategy_public_id = base_strategy.public_id
        base_strategy_id = base_strategy.id
        actor_id = actor.id

    def operation(session: Session, which: int) -> None:
        campaign_row = CampaignRepository(session).get_by_public_id(campaign_public_id)
        StrategyService(session).create_hypothesis(
            campaign=campaign_row, strategy_public_id=base_strategy_public_id,
            statement=f"Attempt {which}.", actor_user_id=actor_id,
        )

    outcomes, errors, backend_pids = _run_two(postgres_engine, operation)
    assert not errors
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
    assert outcomes.count("ok") == 2  # no uniqueness constraint governs this insert

    with Session(postgres_engine) as check:
        rows = list(check.scalars(select(Hypothesis).where(Hypothesis.strategy_id == base_strategy_id)))
        assert len(rows) == 2


# --- Race B: Hypothesis-create vs StrategyRevision, both serializations ------


def test_hypothesis_create_commits_first_then_revision_succeeds(postgres_engine) -> None:
    """Ordering A (MVP-31A §29/§AA): the Hypothesis-create attempt wins the
    Strategy row lock first, commits, then the Revision proceeds
    unaffected — BOTH operations succeed. The new Hypothesis remains
    correctly attached to what becomes the historical base Strategy."""
    with Session(postgres_engine) as setup:
        campaign, _recommendation, _decision, approval, actor = build_strategic_approval(setup)
        base_strategy, _run, _stage = build_base_strategy(setup, campaign=campaign)
        setup.commit()
        campaign_public_id = campaign.public_id
        base_strategy_public_id = base_strategy.public_id
        base_strategy_id = base_strategy.id
        approval_public_id = approval.public_id
        actor_id = actor.id

    ready = Event()
    second_pid: list[int] = []
    with Session(postgres_engine, expire_on_commit=False) as first, ThreadPoolExecutor(max_workers=1) as pool:
        first.scalar(select(Strategy).where(Strategy.id == base_strategy_id).with_for_update())
        first_pid = first.scalar(text("select pg_backend_pid()"))

        def second() -> str:
            with Session(postgres_engine, expire_on_commit=False) as s:
                second_pid.append(s.scalar(text("select pg_backend_pid()")))
                ready.set()
                campaign_row = CampaignRepository(s).get_by_public_id(campaign_public_id)
                try:
                    StrategyRevisionService(s).revise_strategy(
                        campaign=campaign_row, base_strategy_public_id=base_strategy_public_id,
                        strategic_approval_public_id=approval_public_id,
                        summary="Concurrent revision.", positioning_statement="x",
                        actor_user_id=actor_id,
                    )
                    return "ok"
                except ApiError as exc:
                    s.rollback()
                    assert exc.status_code == 409
                    return "conflict"

        task = pool.submit(second)
        assert ready.wait(10)
        assert second_pid[0] != first_pid
        _wait_for_genuine_block(postgres_engine, waiting_pid=second_pid[0], holding_pid=first_pid)

        campaign_row = CampaignRepository(first).get_by_public_id(campaign_public_id)
        StrategyService(first).create_hypothesis(
            campaign=campaign_row, strategy_public_id=base_strategy_public_id,
            statement="Concurrent hypothesis.", actor_user_id=actor_id,
        )
        first.commit()
        first_result = "ok"
        second_result = task.result(timeout=15)

    assert first_result == "ok"
    assert second_result == "ok"  # Revision proceeds once the Hypothesis-create releases the lock

    with Session(postgres_engine) as check:
        hyps = list(check.scalars(select(Hypothesis).where(Hypothesis.strategy_id == base_strategy_id)))
        assert len(hyps) == 1  # remains attached to the now-historical base


def test_revision_commits_first_then_hypothesis_create_is_rejected(postgres_engine) -> None:
    """Ordering B (MVP-31A §29/§AA): StrategyRevision wins the Strategy
    row lock first (as its second lock, after Decision) and commits,
    making the targeted Strategy no longer current before the
    Hypothesis-create attempt proceeds — that attempt must then be
    rejected deterministically, never silently reattached to the new
    current Strategy."""
    with Session(postgres_engine) as setup:
        campaign, _recommendation, _decision, approval, actor = build_strategic_approval(
            setup, campaign_name="Ordering B Campaign"
        )
        base_strategy, _run, _stage = build_base_strategy(setup, campaign=campaign)
        setup.commit()
        campaign_public_id = campaign.public_id
        base_strategy_public_id = base_strategy.public_id
        base_strategy_id = base_strategy.id
        approval_public_id = approval.public_id
        actor_id = actor.id

    ready = Event()
    second_pid: list[int] = []
    with Session(postgres_engine, expire_on_commit=False) as first, ThreadPoolExecutor(max_workers=1) as pool:
        first.scalar(select(Strategy).where(Strategy.id == base_strategy_id).with_for_update())
        first_pid = first.scalar(text("select pg_backend_pid()"))

        def second() -> str:
            with Session(postgres_engine, expire_on_commit=False) as s:
                second_pid.append(s.scalar(text("select pg_backend_pid()")))
                ready.set()
                campaign_row = CampaignRepository(s).get_by_public_id(campaign_public_id)
                try:
                    StrategyService(s).create_hypothesis(
                        campaign=campaign_row, strategy_public_id=base_strategy_public_id,
                        statement="Stale attempt.", actor_user_id=actor_id,
                    )
                    return "ok"
                except ApiError as exc:
                    s.rollback()
                    assert exc.status_code == 409
                    return "conflict"

        task = pool.submit(second)
        assert ready.wait(10)
        assert second_pid[0] != first_pid
        _wait_for_genuine_block(postgres_engine, waiting_pid=second_pid[0], holding_pid=first_pid)

        campaign_row = CampaignRepository(first).get_by_public_id(campaign_public_id)
        StrategyRevisionService(first).revise_strategy(
            campaign=campaign_row, base_strategy_public_id=base_strategy_public_id,
            strategic_approval_public_id=approval_public_id,
            summary="Concurrent revision first.", positioning_statement="x",
            actor_user_id=actor_id,
        )
        first.commit()
        first_result = "ok"
        second_result = task.result(timeout=15)

    assert first_result == "ok"
    assert second_result == "conflict"

    with Session(postgres_engine) as check:
        hyps = list(check.scalars(select(Hypothesis).where(Hypothesis.strategy_id == base_strategy_id)))
        assert len(hyps) == 0  # no Hypothesis created from the rejected attempt
