"""Real PostgreSQL concurrency proof for Governed Content Plan creation
(MVP-33B, frozen MVP-33A/-33A-R1 contract).

The frozen contract intentionally introduces NO new pessimistic lock (no
Strategy/Campaign/Experiment ``FOR UPDATE``, no currency recheck) — the
only concurrency-sensitive operation is the pre-existing ``(campaign_id,
version)`` optimistic race, already backed by a real DB UniqueConstraint +
``IntegrityError`` -> ``VersionConflictError`` catch (``record_plan``'s own
production-proven mechanism, reused unmodified by ``create_plan``). These
tests prove that mechanism genuinely serializes two concurrent creates
without inventing a lock-wait assertion the frozen contract does not call
for.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.campaigns.repository import CampaignRepository
from app.core.api_errors import ApiError
from app.planning.models import ContentPlan
from app.planning.service import PlanningService
from tests.strategytest import build_current_experiment

pytestmark = pytest.mark.postgres


def _run_two(engine, operation):
    """Forces genuine overlap via a two-party ``Barrier`` — without it,
    two threads racing for the same optimistic (campaign_id, version) slot
    are NOT reliably concurrent (one can fully complete, commit, and
    release before the other even starts its own ``next_version_for_
    campaign`` read), which would make the test nondeterministically pass
    both as "ok" instead of genuinely exercising the version-conflict
    path. The barrier holds each thread immediately after opening its own
    session/reading its own backend pid, releasing both at the same
    instant right before they each independently call ``create_plan``
    (and, inside it, independently compute the same "next" version)."""
    outcomes: list[str] = []
    errors: list[Exception] = []
    backend_pids: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def worker(which: int) -> None:
        with Session(engine, expire_on_commit=False) as session:
            pid = session.scalar(text("select pg_backend_pid()"))
            with lock:
                backend_pids.append(pid)
            barrier.wait(timeout=10)
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


def _run_iteration(postgres_engine, *, experiment_public_id: str | None, campaign_public_id: str, actor_id) -> tuple[list[str], list[Exception]]:
    def operation(session: Session, which: int) -> None:
        campaign_row = CampaignRepository(session).get_by_public_id(campaign_public_id)
        PlanningService(session).create_plan(
            campaign=campaign_row, summary=f"Attempt {which}.",
            experiment_public_id=experiment_public_id, actor_user_id=actor_id,
        )

    outcomes, errors, backend_pids = _run_two(postgres_engine, operation)
    assert not errors
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
    return outcomes, errors


# --- C1/C5: generic vs generic, generic vs Experiment-derived ---------------


def test_two_concurrent_generic_plan_creates_serialize_on_version(postgres_engine) -> None:
    with Session(postgres_engine) as setup:
        from tests.planningtest import default_plan_item  # noqa: F401
        from tests.researchtest import build_campaign_run_with_stages
        from tests.contenttest import make_user

        campaign, _run, _stages = build_campaign_run_with_stages(setup, campaign_name="Concurrency Generic Campaign")
        actor = make_user(setup)
        setup.commit()
        campaign_public_id = campaign.public_id
        actor_id = actor.id

    results = []
    for _iteration in range(3):
        outcomes, _errors = _run_iteration(
            postgres_engine, experiment_public_id=None, campaign_public_id=campaign_public_id, actor_id=actor_id
        )
        results.append(outcomes)
        assert outcomes.count("ok") == 1
        assert outcomes.count("conflict") == 1

    with Session(postgres_engine) as check:
        campaign_row = CampaignRepository(check).get_by_public_id(campaign_public_id)
        rows = list(check.scalars(select(ContentPlan).where(ContentPlan.campaign_id == campaign_row.id)))
        versions = [r.version for r in rows]
        assert len(versions) == len(set(versions))  # no duplicate (campaign_id, version)
        assert len(versions) == 3  # exactly one survivor per iteration

    print(f"C1 iteration outcomes: {results}")


def test_generic_vs_experiment_derived_plan_creates_serialize_on_version(postgres_engine) -> None:
    with Session(postgres_engine) as setup:
        campaign, _strategy, _hypothesis, experiment, actor = build_current_experiment(
            setup, campaign_name="Concurrency Mixed Campaign"
        )
        setup.commit()
        campaign_public_id = campaign.public_id
        experiment_public_id = experiment.public_id
        actor_id = actor.id

    outcomes: list[str] = []
    errors: list[Exception] = []
    lock = threading.Lock()
    backend_pids: list[int] = []
    barrier = threading.Barrier(2)

    def generic_op(session: Session, which: int) -> None:
        campaign_row = CampaignRepository(session).get_by_public_id(campaign_public_id)
        PlanningService(session).create_plan(
            campaign=campaign_row, summary="Generic.", experiment_public_id=None, actor_user_id=actor_id,
        )

    def experiment_op(session: Session, which: int) -> None:
        campaign_row = CampaignRepository(session).get_by_public_id(campaign_public_id)
        PlanningService(session).create_plan(
            campaign=campaign_row, summary="Experiment-derived.",
            experiment_public_id=experiment_public_id, actor_user_id=actor_id,
        )

    def worker(op, which: int) -> None:
        with Session(postgres_engine, expire_on_commit=False) as session:
            pid = session.scalar(text("select pg_backend_pid()"))
            with lock:
                backend_pids.append(pid)
            barrier.wait(timeout=10)
            try:
                op(session, which)
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
        futures = [pool.submit(worker, generic_op, 0), pool.submit(worker, experiment_op, 1)]
        for future in futures:
            future.result(timeout=15)

    assert not errors
    assert outcomes.count("ok") == 1
    assert outcomes.count("conflict") == 1

    with Session(postgres_engine) as check:
        campaign_row = CampaignRepository(check).get_by_public_id(campaign_public_id)
        rows = list(check.scalars(select(ContentPlan).where(ContentPlan.campaign_id == campaign_row.id)))
        assert len(rows) == 1  # only the winner persisted
