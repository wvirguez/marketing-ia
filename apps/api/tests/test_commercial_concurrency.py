"""Forced real PostgreSQL lock orderings for CommercialObjective/Offer
supersession (MVP-27), mirroring
``tests/test_strategic_implication_concurrency.py``'s own
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
from app.commercial.models import CommercialObjective, Offer
from app.commercial.service import CommercialService
from app.core.api_errors import ApiError
from tests.commercialtest import build_commercial_objective, build_offer

pytestmark = pytest.mark.postgres


def _run_two(engine, operation):
    """Runs ``operation(session, which)`` on two genuinely separate
    connections/sessions concurrently. Returns ``(outcomes, errors,
    backend_pids)``, mirroring
    ``tests.test_learning_maturation_concurrency._run_two`` exactly."""
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


def test_forced_ordering_two_supersede_attempts_on_the_same_objective(postgres_engine):
    """Both concurrent attempts target the SAME original Objective row —
    the one genuine supersession race. Exactly one succeeds, the other
    deterministically conflicts, regardless of which one is released
    first."""
    with Session(postgres_engine) as setup:
        campaign, original = build_commercial_objective(setup)
        setup.commit()
        campaign_public_id = campaign.public_id
        objective_public_id = original.public_id
        original_id = original.id

    ready = Event()
    second_pid: list[int] = []
    with Session(postgres_engine, expire_on_commit=False) as first, ThreadPoolExecutor(max_workers=1) as pool:
        first.scalar(select(CommercialObjective).where(CommercialObjective.id == original_id).with_for_update())
        first_pid = first.scalar(text("select pg_backend_pid()"))

        def second() -> str:
            with Session(postgres_engine, expire_on_commit=False) as s:
                second_pid.append(s.scalar(text("select pg_backend_pid()")))
                ready.set()
                campaign_row = CampaignRepository(s).get_by_public_id(campaign_public_id)
                try:
                    CommercialService(s).supersede_commercial_objective(
                        campaign=campaign_row, objective_public_id=objective_public_id, statement="Second replacement."
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
                CommercialService(first).supersede_commercial_objective(
                    campaign=campaign_row, objective_public_id=objective_public_id, statement="First replacement."
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
        original_row = check.get(CommercialObjective, original_id)
        rows = list(check.scalars(select(CommercialObjective).where(CommercialObjective.campaign_id == original_row.campaign_id)))
        assert len(rows) == 2  # original + exactly one replacement, never two
        assert original_row.superseded_at is not None
        replacement_row = next(r for r in rows if r.id != original_id)
        assert original_row.superseded_by_commercial_objective_id == replacement_row.id


def test_forced_ordering_two_supersede_attempts_on_the_same_offer(postgres_engine):
    with Session(postgres_engine) as setup:
        campaign, original = build_offer(setup)
        setup.commit()
        campaign_public_id = campaign.public_id
        offer_public_id = original.public_id
        original_id = original.id

    ready = Event()
    second_pid: list[int] = []
    with Session(postgres_engine, expire_on_commit=False) as first, ThreadPoolExecutor(max_workers=1) as pool:
        first.scalar(select(Offer).where(Offer.id == original_id).with_for_update())
        first_pid = first.scalar(text("select pg_backend_pid()"))

        def second() -> str:
            with Session(postgres_engine, expire_on_commit=False) as s:
                second_pid.append(s.scalar(text("select pg_backend_pid()")))
                ready.set()
                campaign_row = CampaignRepository(s).get_by_public_id(campaign_public_id)
                try:
                    CommercialService(s).supersede_offer(
                        campaign=campaign_row, offer_public_id=offer_public_id, statement="Second replacement."
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
                CommercialService(first).supersede_offer(
                    campaign=campaign_row, offer_public_id=offer_public_id, statement="First replacement."
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
        original_row = check.get(Offer, original_id)
        rows = list(check.scalars(select(Offer).where(Offer.campaign_id == original_row.campaign_id)))
        assert len(rows) == 2
        assert original_row.superseded_at is not None


def test_two_independent_offer_creates_for_the_same_campaign_both_succeed(postgres_engine):
    with Session(postgres_engine) as setup:
        campaign, _first = build_offer(setup)
        setup.commit()
        campaign_public_id = campaign.public_id

    def operation(session: Session, which: int) -> None:
        campaign_row = CampaignRepository(session).get_by_public_id(campaign_public_id)
        CommercialService(session).record_offer(campaign=campaign_row, statement=f"Independent offer {which}.")

    outcomes, errors, backend_pids = _run_two(postgres_engine, operation)
    assert not errors
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
    assert outcomes.count("ok") == 2

    with Session(postgres_engine) as check:
        campaign_row = CampaignRepository(check).get_by_public_id(campaign_public_id)
        rows = CommercialService(check).list_offers_for_campaign(campaign_row.id)
        assert len(rows) == 3  # the setup offer + both independent creates


def test_two_independent_objective_creates_for_the_same_campaign_both_succeed(postgres_engine):
    with Session(postgres_engine) as setup:
        campaign, _first = build_commercial_objective(setup)
        setup.commit()
        campaign_public_id = campaign.public_id

    def operation(session: Session, which: int) -> None:
        campaign_row = CampaignRepository(session).get_by_public_id(campaign_public_id)
        CommercialService(session).record_commercial_objective(campaign=campaign_row, statement=f"Independent objective {which}.")

    outcomes, errors, backend_pids = _run_two(postgres_engine, operation)
    assert not errors
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
    assert outcomes.count("ok") == 2

    with Session(postgres_engine) as check:
        campaign_row = CampaignRepository(check).get_by_public_id(campaign_public_id)
        rows = CommercialService(check).list_objectives_for_campaign(campaign_row.id)
        assert len(rows) == 3


def test_different_offers_are_independently_supersedable_without_campaign_wide_serialization(postgres_engine):
    """MVP-27A-R1 §Q/§19: different Offers under the same Campaign must
    not require Campaign-level serialization — two genuinely concurrent
    supersessions against two DIFFERENT Offer rows both succeed."""
    with Session(postgres_engine) as setup:
        campaign, offer_a = build_offer(setup, statement="Offer A")
        offer_b = CommercialService(setup).record_offer(campaign=campaign, statement="Offer B")
        setup.commit()
        campaign_public_id = campaign.public_id
        offer_a_public_id = offer_a.public_id
        offer_b_public_id = offer_b.public_id

    def operation(session: Session, which: int) -> None:
        campaign_row = CampaignRepository(session).get_by_public_id(campaign_public_id)
        target = offer_a_public_id if which == 0 else offer_b_public_id
        CommercialService(session).supersede_offer(campaign=campaign_row, offer_public_id=target, statement=f"Replacement {which}.")

    outcomes, errors, backend_pids = _run_two(postgres_engine, operation)
    assert not errors
    assert outcomes.count("ok") == 2  # both succeed — no Campaign-wide lock forces one to wait/conflict
