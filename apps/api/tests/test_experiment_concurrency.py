"""Forced real PostgreSQL lock orderings for Governed Experiment creation
(MVP-32B, frozen MVP-32A/-32A-R1 contract), mirroring
``tests/test_hypothesis_concurrency.py``'s own ``pg_blocking_pids()``-based
technique exactly — the second connection must be observed genuinely
blocked by PostgreSQL before the first operation runs. No sleep-based
order, no random winner.

Experiment-create locks the Strategy row referenced by its target
Hypothesis's own strategy_id (MVP-32A-R1 §9) — the exact same single-
resource lock shape as Hypothesis-create, so it cannot deadlock against
StrategyRevision's own Decision-then-Strategy lock order (same
acyclicity proof, one level down).
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
from app.orchestration.models import StrategicApprovalOutcome, StrategicDecisionType
from app.orchestration.service import StrategicApprovalService, StrategicDecisionService, StrategyRevisionService
from app.strategy.models import Experiment, Strategy
from app.strategy.repository import HypothesisRepository
from app.strategy.service import StrategyService
from tests.strategytest import build_current_hypothesis

pytestmark = pytest.mark.postgres


def _run_two(engine, operation):
    """Mirrors ``tests.test_hypothesis_concurrency._run_two`` exactly."""
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


# --- Race A: two concurrent creates under the same still-current Hypothesis --


def test_two_concurrent_creates_under_the_same_hypothesis_both_succeed(postgres_engine) -> None:
    with Session(postgres_engine) as setup:
        campaign, strategy, hypothesis, actor = build_current_hypothesis(setup)
        setup.commit()
        campaign_public_id = campaign.public_id
        hypothesis_public_id = hypothesis.public_id
        hypothesis_id = hypothesis.id
        actor_id = actor.id

    def operation(session: Session, which: int) -> None:
        campaign_row = CampaignRepository(session).get_by_public_id(campaign_public_id)
        StrategyService(session).create_experiment(
            campaign=campaign_row, hypothesis_public_id=hypothesis_public_id,
            description=f"Attempt {which}.", actor_user_id=actor_id,
        )

    outcomes, errors, backend_pids = _run_two(postgres_engine, operation)
    assert not errors
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
    assert outcomes.count("ok") == 2  # serialized on the shared Strategy lock, but both succeed

    with Session(postgres_engine) as check:
        rows = list(check.scalars(select(Experiment).where(Experiment.hypothesis_id == hypothesis_id)))
        assert len(rows) == 2


def test_same_hypothesis_race_demonstrates_genuine_strategy_lock_contention(postgres_engine) -> None:
    """Adversarial proof (avoiding the exact MVP31C-OBS-1 test-rigor gap):
    forces the first transaction to hold the Strategy row's FOR UPDATE
    lock open and confirms via genuine pg_blocking_pids() polling that the
    second Experiment-create attempt genuinely blocks on it."""
    with Session(postgres_engine) as setup:
        campaign, strategy, hypothesis, actor = build_current_hypothesis(setup)
        setup.commit()
        campaign_public_id = campaign.public_id
        hypothesis_public_id = hypothesis.public_id
        strategy_id = strategy.id
        actor_id = actor.id

    ready = Event()
    second_pid: list[int] = []
    blocked_observed = [False]

    with Session(postgres_engine, expire_on_commit=False) as first, ThreadPoolExecutor(max_workers=1) as pool:
        first.scalar(select(Strategy).where(Strategy.id == strategy_id).with_for_update())
        first_pid = first.scalar(text("select pg_backend_pid()"))

        def second() -> str:
            with Session(postgres_engine, expire_on_commit=False) as s:
                second_pid.append(s.scalar(text("select pg_backend_pid()")))
                ready.set()
                campaign_row = CampaignRepository(s).get_by_public_id(campaign_public_id)
                StrategyService(s).create_experiment(
                    campaign=campaign_row, hypothesis_public_id=hypothesis_public_id,
                    description="second", actor_user_id=actor_id,
                )
                return "ok"

        task = pool.submit(second)
        assert ready.wait(10)
        deadline = monotonic() + 5
        with postgres_engine.connect() as observer:
            while monotonic() < deadline:
                blockers = observer.execute(text("select pg_blocking_pids(:pid)"), {"pid": second_pid[0]}).scalar_one()
                observer.commit()
                if first_pid in blockers:
                    blocked_observed[0] = True
                    break
                sleep(0.01)
        first.commit()
        result = task.result(timeout=15)

    assert blocked_observed[0] is True
    assert result == "ok"


# --- Race B: Experiment-create vs StrategyRevision, both serializations ------


def test_experiment_create_commits_first_then_revision_succeeds(postgres_engine) -> None:
    """Ordering A (MVP-32A-R1 §17): the Experiment-create attempt wins the
    Strategy row lock first, commits, then the Revision proceeds
    unaffected — BOTH operations succeed. The Experiment remains
    correctly attached (via its Hypothesis) to what becomes the
    historical Strategy."""
    with Session(postgres_engine) as setup:
        campaign, strategy, hypothesis, actor = build_current_hypothesis(setup)
        approval = _second_eligible_approval(setup, campaign=campaign, actor_id=actor.id)
        setup.commit()
        campaign_public_id = campaign.public_id
        hypothesis_public_id = hypothesis.public_id
        strategy_public_id = strategy.public_id
        strategy_id = strategy.id
        approval_public_id = approval.public_id
        actor_id = actor.id

    ready = Event()
    second_pid: list[int] = []
    with Session(postgres_engine, expire_on_commit=False) as first, ThreadPoolExecutor(max_workers=1) as pool:
        first.scalar(select(Strategy).where(Strategy.id == strategy_id).with_for_update())
        first_pid = first.scalar(text("select pg_backend_pid()"))

        def second() -> str:
            with Session(postgres_engine, expire_on_commit=False) as s:
                second_pid.append(s.scalar(text("select pg_backend_pid()")))
                ready.set()
                campaign_row = CampaignRepository(s).get_by_public_id(campaign_public_id)
                try:
                    StrategyRevisionService(s).revise_strategy(
                        campaign=campaign_row, base_strategy_public_id=strategy_public_id,
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
        StrategyService(first).create_experiment(
            campaign=campaign_row, hypothesis_public_id=hypothesis_public_id,
            description="Concurrent experiment.", actor_user_id=actor_id,
        )
        first.commit()
        first_result = "ok"
        second_result = task.result(timeout=15)

    assert first_result == "ok"
    assert second_result == "ok"  # Revision proceeds once the Experiment-create releases the lock

    with Session(postgres_engine) as check:
        hyp = HypothesisRepository(check).get_for_campaign_by_public_id(
            campaign_id=CampaignRepository(check).get_by_public_id(campaign_public_id).id,
            public_id=hypothesis_public_id,
        )
        exps = list(check.scalars(select(Experiment).where(Experiment.hypothesis_id == hyp.id)))
        assert len(exps) == 1  # remains attached to the now-historical base, via its Hypothesis


def test_revision_commits_first_then_experiment_create_is_rejected(postgres_engine) -> None:
    """Ordering B (MVP-32A-R1 §17): StrategyRevision wins the Strategy row
    lock first and commits, making the targeted Strategy no longer
    current before the Experiment-create attempt proceeds — that attempt
    must then be rejected deterministically, never silently reattached to
    the new current Strategy."""
    with Session(postgres_engine) as setup:
        campaign, strategy, hypothesis, actor = build_current_hypothesis(setup, campaign_name="Ordering B Campaign")
        approval = _second_eligible_approval(setup, campaign=campaign, actor_id=actor.id)
        setup.commit()
        campaign_public_id = campaign.public_id
        hypothesis_public_id = hypothesis.public_id
        strategy_public_id = strategy.public_id
        strategy_id = strategy.id
        approval_public_id = approval.public_id
        actor_id = actor.id

    ready = Event()
    second_pid: list[int] = []
    with Session(postgres_engine, expire_on_commit=False) as first, ThreadPoolExecutor(max_workers=1) as pool:
        first.scalar(select(Strategy).where(Strategy.id == strategy_id).with_for_update())
        first_pid = first.scalar(text("select pg_backend_pid()"))

        def second() -> str:
            with Session(postgres_engine, expire_on_commit=False) as s:
                second_pid.append(s.scalar(text("select pg_backend_pid()")))
                ready.set()
                campaign_row = CampaignRepository(s).get_by_public_id(campaign_public_id)
                try:
                    StrategyService(s).create_experiment(
                        campaign=campaign_row, hypothesis_public_id=hypothesis_public_id,
                        description="Stale attempt.", actor_user_id=actor_id,
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
            campaign=campaign_row, base_strategy_public_id=strategy_public_id,
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
        hyp = HypothesisRepository(check).get_for_campaign_by_public_id(
            campaign_id=CampaignRepository(check).get_by_public_id(campaign_public_id).id,
            public_id=hypothesis_public_id,
        )
        exps = list(check.scalars(select(Experiment).where(Experiment.hypothesis_id == hyp.id)))
        assert len(exps) == 0  # no Experiment created from the rejected attempt


# --- helpers -------------------------------------------------------------------


def _second_eligible_approval(session, *, campaign, actor_id):
    """Builds a full, independent StrategicApproval ancestry within the
    SAME already-existing ``campaign`` — mirrors
    ``tests/test_strategy_revision_concurrency.py::_second_eligible_approval``
    exactly."""
    from app.learning.models import StrategicRecommendationDecision
    from app.learning.service import LearningService
    from tests.test_strategic_decision_api import _build_recommendation_in_campaign

    recommendation = _build_recommendation_in_campaign(session, campaign)
    accepted = LearningService(session).decide_strategic_recommendation_candidate(
        campaign=campaign, recommendation_public_id=recommendation.public_id,
        decision=StrategicRecommendationDecision.ACCEPTED, actor_user_id=actor_id,
    )
    decision = StrategicDecisionService(session).record_decision(
        campaign=campaign, recommendation_public_id=accepted.public_id,
        decision_type=StrategicDecisionType.ADOPT, statement="Second decision.", actor_user_id=actor_id,
    )
    return StrategicApprovalService(session).record_approval(
        campaign=campaign, decision_public_id=decision.public_id,
        outcome=StrategicApprovalOutcome.APPROVED, actor_user_id=actor_id,
    )
