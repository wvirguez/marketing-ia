"""Forced real PostgreSQL lock orderings for Governed Strategy Revision
(MVP-30B, frozen MVP-30A/-30A-R1 contract), mirroring
``tests/test_strategic_approval_concurrency.py``'s own
``pg_blocking_pids()``-based technique exactly — the second connection
must be observed genuinely blocked by PostgreSQL before the first
operation runs. No sleep-based order, no random winner.

Both Revision/Supersession orderings require GENUINE concurrent lock
evidence from the start (MVP-30A-R1 §23/§AD, MVP-30B §49/§50) — explicitly
closing the exact test-rigor gap ``MVP29C-OBS-2`` found after the fact for
``StrategicApproval`` (where only one of two symmetric orderings had real
blocking evidence in the persisted suite).
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
from app.orchestration.models import StrategicDecision, StrategicDecisionType, StrategyRevision
from app.orchestration.service import StrategicDecisionService, StrategyRevisionService
from app.strategy.models import Strategy
from tests.orchestrationtest import build_base_strategy, build_strategic_approval

pytestmark = pytest.mark.postgres


def _run_two(engine, operation):
    """Mirrors ``tests.test_strategic_approval_concurrency._run_two``
    exactly."""
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


# --- Race A: two concurrent first Revisions for the same Approval ----------


def test_two_concurrent_revisions_for_the_same_approval_exactly_one_succeeds(postgres_engine) -> None:
    with Session(postgres_engine) as setup:
        campaign, _recommendation, _decision, approval, actor = build_strategic_approval(setup)
        base_strategy, _run, _stage = build_base_strategy(setup, campaign=campaign)
        setup.commit()
        campaign_public_id = campaign.public_id
        base_strategy_public_id = base_strategy.public_id
        approval_id = approval.id
        approval_public_id = approval.public_id
        actor_id = actor.id

    def operation(session: Session, which: int) -> None:
        campaign_row = CampaignRepository(session).get_by_public_id(campaign_public_id)
        StrategyRevisionService(session).revise_strategy(
            campaign=campaign_row, base_strategy_public_id=base_strategy_public_id,
            strategic_approval_public_id=approval_public_id,
            summary=f"Attempt {which}.", positioning_statement=f"Positioning {which}.",
            actor_user_id=actor_id,
        )

    outcomes, errors, backend_pids = _run_two(postgres_engine, operation)
    assert not errors
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
    assert outcomes.count("ok") == 1
    assert outcomes.count("conflict") == 1

    with Session(postgres_engine) as check:
        rows = list(
            check.scalars(select(StrategyRevision).where(StrategyRevision.strategic_approval_id == approval_id))
        )
        assert len(rows) == 1  # never two Revisions for the same Approval


# --- Race B: two concurrent first Revisions for the same base Strategy ------


def test_two_concurrent_revisions_from_the_same_base_strategy_exactly_one_succeeds(postgres_engine) -> None:
    with Session(postgres_engine) as setup:
        campaign, _recommendation, _decision, approval_a, actor = build_strategic_approval(setup, campaign_name="Race B Campaign")
        base_strategy, _run, _stage = build_base_strategy(setup, campaign=campaign)
        setup.commit()
        campaign_public_id = campaign.public_id
        base_strategy_public_id = base_strategy.public_id
        base_strategy_id = base_strategy.id
        approval_a_public_id = approval_a.public_id
        actor_id = actor.id

    with Session(postgres_engine) as setup2:
        campaign_row = CampaignRepository(setup2).get_by_public_id(campaign_public_id)
        approval_b = _second_eligible_approval(setup2, campaign=campaign_row, actor_id=actor_id)
        setup2.commit()
        approval_b_public_id = approval_b.public_id

    def operation(session: Session, which: int) -> None:
        campaign_row = CampaignRepository(session).get_by_public_id(campaign_public_id)
        approval_public_id = approval_a_public_id if which == 0 else approval_b_public_id
        StrategyRevisionService(session).revise_strategy(
            campaign=campaign_row, base_strategy_public_id=base_strategy_public_id,
            strategic_approval_public_id=approval_public_id,
            summary=f"Attempt {which}.", positioning_statement=f"Positioning {which}.",
            actor_user_id=actor_id,
        )

    outcomes, errors, backend_pids = _run_two(postgres_engine, operation)
    assert not errors
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
    assert outcomes.count("ok") == 1
    assert outcomes.count("conflict") == 1

    with Session(postgres_engine) as check:
        rows = list(
            check.scalars(select(Strategy).where(Strategy.campaign_id == campaign_row_id(check, campaign_public_id)))
        )
        # base + exactly one successful result, never two.
        result_versions = {s.version for s in rows} - {base_strategy_row(check, base_strategy_id).version}
        assert len(result_versions) == 1


# --- Race C: Revision vs Decision supersession, both serializations --------


def test_revision_commits_first_then_supersession_succeeds_and_revision_remains_valid(postgres_engine) -> None:
    """Ordering B1 (MVP-30A-R1 §M / MVP-30B §49): the Revision attempt
    wins the Decision row lock first, commits, then the supersession
    attempt proceeds — BOTH operations succeed. StrategyRevision remains
    permanently valid regardless of its Decision's later supersession."""
    with Session(postgres_engine) as setup:
        campaign, _recommendation, decision, approval, actor = build_strategic_approval(setup)
        base_strategy, _run, _stage = build_base_strategy(setup, campaign=campaign)
        setup.commit()
        campaign_public_id = campaign.public_id
        base_strategy_public_id = base_strategy.public_id
        decision_id = decision.id
        decision_public_id = decision.public_id
        approval_public_id = approval.public_id
        actor_id = actor.id

    ready = Event()
    second_pid: list[int] = []
    with Session(postgres_engine, expire_on_commit=False) as first, ThreadPoolExecutor(max_workers=1) as pool:
        first.scalar(select(StrategicDecision).where(StrategicDecision.id == decision_id).with_for_update())
        first_pid = first.scalar(text("select pg_backend_pid()"))

        def second() -> str:
            with Session(postgres_engine, expire_on_commit=False) as s:
                second_pid.append(s.scalar(text("select pg_backend_pid()")))
                ready.set()
                campaign_row = CampaignRepository(s).get_by_public_id(campaign_public_id)
                try:
                    StrategicDecisionService(s).supersede_decision(
                        campaign=campaign_row, decision_public_id=decision_public_id,
                        decision_type=StrategicDecisionType.DEFER, statement="Concurrent supersession.",
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
        StrategyRevisionService(first).revise_strategy(
            campaign=campaign_row, base_strategy_public_id=base_strategy_public_id,
            strategic_approval_public_id=approval_public_id,
            summary="Concurrent revision.", positioning_statement="Concurrent positioning.",
            actor_user_id=actor_id,
        )
        first.commit()
        first_result = "ok"
        second_result = task.result(timeout=15)

    assert first_result == "ok"
    assert second_result == "ok"  # supersession proceeds once the Revision releases the lock

    with Session(postgres_engine) as check:
        revision = check.scalar(select(StrategyRevision).where(StrategyRevision.base_strategy_id.isnot(None)).order_by(StrategyRevision.created_at.desc()))
        assert revision is not None
        original = check.get(StrategicDecision, decision_id)
        assert original.superseded_at is not None  # historical, but Revision remains valid and attached


def test_supersession_commits_first_then_revision_is_rejected(postgres_engine) -> None:
    """Ordering B2 (MVP-30A-R1 §N / MVP-30B §50): the supersession attempt
    wins the Decision row lock first and commits, making the Decision
    non-current before the Revision attempt ever proceeds — the Revision
    attempt must then be rejected deterministically, with no Strategy/
    Positioning/StrategyRevision/AuditEvent row created."""
    with Session(postgres_engine) as setup:
        campaign, _recommendation, decision, approval, actor = build_strategic_approval(setup, campaign_name="Ordering B2 Campaign")
        base_strategy, _run, _stage = build_base_strategy(setup, campaign=campaign)
        setup.commit()
        campaign_public_id = campaign.public_id
        base_strategy_public_id = base_strategy.public_id
        decision_id = decision.id
        decision_public_id = decision.public_id
        approval_public_id = approval.public_id
        approval_id = approval.id
        actor_id = actor.id

    ready = Event()
    second_pid: list[int] = []
    with Session(postgres_engine, expire_on_commit=False) as first, ThreadPoolExecutor(max_workers=1) as pool:
        first.scalar(select(StrategicDecision).where(StrategicDecision.id == decision_id).with_for_update())
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
                        summary="Stale revision attempt.", positioning_statement="x",
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
        StrategicDecisionService(first).supersede_decision(
            campaign=campaign_row, decision_public_id=decision_public_id,
            decision_type=StrategicDecisionType.DEFER, statement="Concurrent supersession first.",
            actor_user_id=actor_id,
        )
        first.commit()
        first_result = "ok"
        second_result = task.result(timeout=15)

    assert first_result == "ok"
    assert second_result == "conflict"

    with Session(postgres_engine) as check:
        revision = check.scalar(select(StrategyRevision).where(StrategyRevision.strategic_approval_id == approval_id))
        assert revision is None  # no Revision created from the rejected attempt
        strategies = list(check.scalars(select(Strategy).where(Strategy.campaign_id == campaign_row_id(check, campaign_public_id))))
        assert len(strategies) == 1  # only the original base Strategy — no partial result


# --- helpers -------------------------------------------------------------------


def _second_eligible_approval(session, *, campaign, actor_id):
    from app.learning.models import StrategicRecommendationDecision
    from app.learning.service import LearningService
    from app.orchestration.models import StrategicApprovalOutcome
    from app.orchestration.service import StrategicApprovalService
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


def campaign_row_id(session, campaign_public_id: str):
    return CampaignRepository(session).get_by_public_id(campaign_public_id).id


def base_strategy_row(session, base_strategy_id):
    return session.get(Strategy, base_strategy_id)
