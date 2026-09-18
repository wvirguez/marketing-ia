"""Forced real PostgreSQL lock orderings for StrategicApproval (MVP-29B,
frozen MVP-29A contract), mirroring
``tests/test_strategic_decision_concurrency.py``'s own
``pg_blocking_pids()``-based technique exactly — the second connection
must be observed genuinely blocked by PostgreSQL before the first
operation runs. No sleep-based order, no random winner.
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
from app.orchestration.models import StrategicApproval, StrategicApprovalOutcome, StrategicDecision, StrategicDecisionType
from app.orchestration.service import StrategicApprovalService, StrategicDecisionService
from tests.orchestrationtest import build_strategic_decision

pytestmark = pytest.mark.postgres


def _run_two(engine, operation):
    """Mirrors ``tests.test_strategic_decision_concurrency._run_two``
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


# --- Race A: two concurrent first Approvals for the same Decision ----------


def test_two_concurrent_first_approvals_for_the_same_decision_exactly_one_succeeds(postgres_engine) -> None:
    with Session(postgres_engine) as setup:
        campaign, _recommendation, decision, actor = build_strategic_decision(
            setup, decision_type=StrategicDecisionType.ADOPT
        )
        setup.commit()
        campaign_public_id = campaign.public_id
        decision_public_id = decision.public_id
        decision_id = decision.id
        actor_id = actor.id

    def operation(session: Session, which: int) -> None:
        campaign_row = CampaignRepository(session).get_by_public_id(campaign_public_id)
        StrategicApprovalService(session).record_approval(
            campaign=campaign_row, decision_public_id=decision_public_id,
            outcome=StrategicApprovalOutcome.APPROVED, actor_user_id=actor_id,
        )

    outcomes, errors, backend_pids = _run_two(postgres_engine, operation)
    assert not errors
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
    assert outcomes.count("ok") == 1
    assert outcomes.count("conflict") == 1

    with Session(postgres_engine) as check:
        rows = list(
            check.scalars(select(StrategicApproval).where(StrategicApproval.strategic_decision_id == decision_id))
        )
        assert len(rows) == 1  # never two Approvals for the same Decision


# --- Race B: APPROVED vs REJECTED for the same Decision ---------------------


def test_approved_vs_rejected_race_yields_exactly_one_terminal_outcome(postgres_engine) -> None:
    with Session(postgres_engine) as setup:
        campaign, _recommendation, decision, actor = build_strategic_decision(
            setup, decision_type=StrategicDecisionType.ADOPT
        )
        setup.commit()
        campaign_public_id = campaign.public_id
        decision_public_id = decision.public_id
        decision_id = decision.id
        actor_id = actor.id

    def operation(session: Session, which: int) -> None:
        campaign_row = CampaignRepository(session).get_by_public_id(campaign_public_id)
        outcome = StrategicApprovalOutcome.APPROVED if which == 0 else StrategicApprovalOutcome.REJECTED
        StrategicApprovalService(session).record_approval(
            campaign=campaign_row, decision_public_id=decision_public_id,
            outcome=outcome, actor_user_id=actor_id,
        )

    outcomes, errors, backend_pids = _run_two(postgres_engine, operation)
    assert not errors
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
    assert outcomes.count("ok") == 1
    assert outcomes.count("conflict") == 1

    with Session(postgres_engine) as check:
        rows = list(
            check.scalars(select(StrategicApproval).where(StrategicApproval.strategic_decision_id == decision_id))
        )
        assert len(rows) == 1  # exactly one terminal outcome, never both


# --- Race C: Approval vs Decision supersession, both serializations --------


def test_approval_commits_first_then_supersession_succeeds_and_approval_remains_historical(postgres_engine) -> None:
    """Valid serialization 1 (MVP-29B §15 Race C): the Approval attempt
    wins the row lock first, commits, then the supersession attempt
    proceeds against the now-historical (but still ADOPT-at-the-time)
    Decision — supersession itself never checks Approval eligibility, so
    it always succeeds regardless of ordering; what this proves is that
    the Approval that committed first survives, unmodified, as a
    historical fact."""
    with Session(postgres_engine) as setup:
        campaign, _recommendation, decision, actor = build_strategic_decision(
            setup, decision_type=StrategicDecisionType.ADOPT
        )
        setup.commit()
        campaign_public_id = campaign.public_id
        decision_public_id = decision.public_id
        decision_id = decision.id
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
        StrategicApprovalService(first).record_approval(
            campaign=campaign_row, decision_public_id=decision_public_id,
            outcome=StrategicApprovalOutcome.APPROVED, actor_user_id=actor_id,
        )
        first.commit()
        first_result = "ok"
        second_result = task.result(timeout=15)

    assert first_result == "ok"
    assert second_result == "ok"  # supersession proceeds once the Approval releases the lock

    with Session(postgres_engine) as check:
        approval = check.scalar(select(StrategicApproval).where(StrategicApproval.strategic_decision_id == decision_id))
        assert approval is not None
        assert approval.outcome is StrategicApprovalOutcome.APPROVED
        original = check.get(StrategicDecision, decision_id)
        assert original.superseded_at is not None  # historical, but Approval remains attached and true


def test_approval_attempt_against_a_decision_already_superseded_by_a_genuine_race_is_rejected(postgres_engine) -> None:
    """Valid serialization 2 (MVP-29B §15 Race C): the supersession
    attempt wins the row lock first and commits, making the Decision
    non-current before the Approval attempt ever proceeds — the Approval
    attempt must then be rejected deterministically (never silently
    approve an already-superseded Decision, and never approve the
    replacement instead by surprise)."""
    with Session(postgres_engine) as setup:
        campaign, _recommendation, decision, actor = build_strategic_decision(
            setup, decision_type=StrategicDecisionType.ADOPT
        )
        setup.commit()
        campaign_public_id = campaign.public_id
        decision_public_id = decision.public_id
        actor_id = actor.id

    with Session(postgres_engine) as winner:
        campaign_row = CampaignRepository(winner).get_by_public_id(campaign_public_id)
        StrategicDecisionService(winner).supersede_decision(
            campaign=campaign_row, decision_public_id=decision_public_id,
            decision_type=StrategicDecisionType.DEFER, statement="Superseded before the stale Approval attempt.",
            actor_user_id=actor_id,
        )
        winner.commit()

    with Session(postgres_engine) as stale:
        campaign_row = CampaignRepository(stale).get_by_public_id(campaign_public_id)
        with pytest.raises(ApiError) as excinfo:
            StrategicApprovalService(stale).record_approval(
                campaign=campaign_row, decision_public_id=decision_public_id,
                outcome=StrategicApprovalOutcome.APPROVED, actor_user_id=actor_id,
            )
        assert excinfo.value.status_code == 409
        assert excinfo.value.code == "STRATEGIC_DECISION_NOT_ELIGIBLE_FOR_APPROVAL"
