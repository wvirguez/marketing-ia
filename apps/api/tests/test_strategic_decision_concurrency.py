"""Forced real PostgreSQL lock orderings for StrategicDecision (MVP-28B,
frozen MVP-28A/-R1/-R2 contract), mirroring
``tests/test_commercial_concurrency.py``'s own ``pg_blocking_pids()``-based
technique exactly — the second connection must be observed genuinely
blocked by PostgreSQL before the first operation runs. No sleep-based
order, no random winner.
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
from app.orchestration.models import StrategicDecision, StrategicDecisionType
from app.orchestration.service import StrategicDecisionService
from tests.contenttest import make_user
from tests.orchestrationtest import build_accepted_recommendation, build_strategic_decision

pytestmark = pytest.mark.postgres


def _run_two(engine, operation):
    """Runs ``operation(session, which)`` on two genuinely separate
    connections/sessions concurrently. Returns ``(outcomes, errors,
    backend_pids)``, mirroring ``tests.test_commercial_concurrency._run_two``
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


def test_two_concurrent_first_decisions_for_the_same_recommendation_exactly_one_succeeds(postgres_engine) -> None:
    """At most one current StrategicDecision per Recommendation — two
    genuinely concurrent "record the first Decision" attempts against the
    SAME accepted Recommendation must yield exactly one success and one
    deterministic conflict, never two current rows."""
    with Session(postgres_engine) as setup:
        campaign, recommendation, actor = build_accepted_recommendation(setup)
        setup.commit()
        campaign_public_id = campaign.public_id
        recommendation_public_id = recommendation.public_id
        actor_id = actor.id

    def operation(session: Session, which: int) -> None:
        campaign_row = CampaignRepository(session).get_by_public_id(campaign_public_id)
        StrategicDecisionService(session).record_decision(
            campaign=campaign_row, recommendation_public_id=recommendation_public_id,
            decision_type=StrategicDecisionType.ADOPT, statement=f"Attempt {which}.", actor_user_id=actor_id,
        )

    outcomes, errors, backend_pids = _run_two(postgres_engine, operation)
    assert not errors
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
    assert outcomes.count("ok") == 1
    assert outcomes.count("conflict") == 1

    with Session(postgres_engine) as check:
        rows = list(
            check.scalars(
                select(StrategicDecision).where(
                    StrategicDecision.strategic_recommendation_candidate_id == recommendation.id
                )
            )
        )
        assert len(rows) == 1  # never two current rows for the same Recommendation
        assert rows[0].superseded_at is None


def test_two_concurrent_supersede_attempts_on_the_same_decision_exactly_one_succeeds(postgres_engine) -> None:
    """Both concurrent attempts target the SAME original Decision row —
    the one genuine supersession race. Exactly one succeeds, the other
    deterministically conflicts, regardless of which one is released
    first."""
    with Session(postgres_engine) as setup:
        campaign, _recommendation, original, actor = build_strategic_decision(setup)
        setup.commit()
        campaign_public_id = campaign.public_id
        decision_public_id = original.public_id
        original_id = original.id
        actor_id = actor.id

    ready = Event()
    second_pid: list[int] = []
    with Session(postgres_engine, expire_on_commit=False) as first, ThreadPoolExecutor(max_workers=1) as pool:
        first.scalar(select(StrategicDecision).where(StrategicDecision.id == original_id).with_for_update())
        first_pid = first.scalar(text("select pg_backend_pid()"))

        def second() -> str:
            with Session(postgres_engine, expire_on_commit=False) as s:
                second_pid.append(s.scalar(text("select pg_backend_pid()")))
                ready.set()
                campaign_row = CampaignRepository(s).get_by_public_id(campaign_public_id)
                try:
                    StrategicDecisionService(s).supersede_decision(
                        campaign=campaign_row, decision_public_id=decision_public_id,
                        decision_type=StrategicDecisionType.DEFER, statement="Second replacement.",
                        actor_user_id=actor_id,
                    )
                    return "ok"
                except ApiError as exc:
                    s.rollback()
                    assert exc.status_code == 409
                    return "conflict"

        task = pool.submit(second)
        try:
            assert ready.wait(10)
            assert second_pid[0] != first_pid
            _wait_for_genuine_block(postgres_engine, waiting_pid=second_pid[0], holding_pid=first_pid)

            campaign_row = CampaignRepository(first).get_by_public_id(campaign_public_id)
            try:
                StrategicDecisionService(first).supersede_decision(
                    campaign=campaign_row, decision_public_id=decision_public_id,
                    decision_type=StrategicDecisionType.DECLINE, statement="First replacement.",
                    actor_user_id=actor_id,
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
        original_row = check.get(StrategicDecision, original_id)
        rows = list(
            check.scalars(
                select(StrategicDecision).where(
                    StrategicDecision.strategic_recommendation_candidate_id
                    == original_row.strategic_recommendation_candidate_id
                )
            )
        )
        assert len(rows) == 2  # original + exactly one replacement, never two
        assert original_row.superseded_at is not None
        replacement_row = next(r for r in rows if r.id != original_id)
        assert original_row.superseded_by_strategic_decision_id == replacement_row.id
        assert replacement_row.superseded_at is None


def test_superseding_an_already_stale_decision_after_a_genuine_race_is_rejected(postgres_engine) -> None:
    """A caller holding a reference to a Decision that a concurrent
    operation has *already* fully superseded (not merely racing for the
    lock) must be rejected deterministically, never silently create a
    second current Decision."""
    with Session(postgres_engine) as setup:
        campaign, _recommendation, original, actor = build_strategic_decision(setup)
        setup.commit()
        campaign_public_id = campaign.public_id
        decision_public_id = original.public_id
        actor_id = actor.id

    with Session(postgres_engine) as winner:
        campaign_row = CampaignRepository(winner).get_by_public_id(campaign_public_id)
        StrategicDecisionService(winner).supersede_decision(
            campaign=campaign_row, decision_public_id=decision_public_id,
            decision_type=StrategicDecisionType.DEFER, statement="Already superseded before the stale attempt.",
            actor_user_id=actor_id,
        )
        winner.commit()

    with Session(postgres_engine) as stale:
        campaign_row = CampaignRepository(stale).get_by_public_id(campaign_public_id)
        with pytest.raises(ApiError) as excinfo:
            StrategicDecisionService(stale).supersede_decision(
                campaign=campaign_row, decision_public_id=decision_public_id,
                decision_type=StrategicDecisionType.DECLINE, statement="Stale attempt.", actor_user_id=actor_id,
            )
        assert excinfo.value.status_code == 409
