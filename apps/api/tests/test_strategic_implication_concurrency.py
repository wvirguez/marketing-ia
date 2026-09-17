"""Forced real PostgreSQL lock orderings for StrategicImplication (MVP-26),
mirroring ``tests/test_learning_qualification_concurrency.py``'s own
``pg_blocking_pids()``-based technique exactly — the second connection
must be observed genuinely blocked by PostgreSQL before the first
operation runs. No sleep-based order, no random winner.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

import pytest
import threading
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.campaigns.repository import CampaignRepository
from app.core.api_errors import ApiError
from app.learning.models import LearningCandidate, LearningCandidateStatus, StrategicImplication
from app.learning.service import LearningService
from tests.learningtest import seed_sufficient_qualification
from tests.test_learning_maturation_concurrency import _candidate_at, _run_two

pytestmark = pytest.mark.postgres


def _pending_candidate(engine):
    """Builds one candidate already at VALIDATION_PENDING with sufficient
    qualification already seeded — one HTTP-free step short of VALIDATED,
    so the race below is exactly "does the transition or the implication
    creation observe VALIDATED first," never a race over qualification
    sufficiency itself (already satisfied for both orderings)."""
    return _candidate_at(engine, LearningCandidateStatus.VALIDATION_PENDING)


def _perform(session: Session, cid, operation: str) -> None:
    if operation == "validate":
        candidate = session.get(LearningCandidate, cid)
        LearningService(session).transition_learning_candidate(
            learning_candidate=candidate, target_status=LearningCandidateStatus.VALIDATED
        )
    else:
        candidate = session.get(LearningCandidate, cid)
        LearningService(session).record_strategic_implication(
            learning_candidate=candidate, statement="Race fixture implication."
        )


def _outcome(session: Session, cid, operation: str) -> str:
    try:
        _perform(session, cid, operation)
        return "ok"
    except ApiError as exc:
        session.rollback()
        assert exc.status_code == 409
        return "conflict"


@pytest.mark.parametrize("reverse", [False, True])
def test_forced_ordering_implication_create_vs_validated_transition(postgres_engine, reverse):
    """MVP-26 §12/§41, MVP-26A-R1 §27: the one genuine race between
    StrategicImplication creation and the VALIDATED transition — both
    acquire the same LearningCandidate FOR UPDATE lock (canonical
    topology, no second lock).

    reverse=False (implication attempted first, while still
    VALIDATION_PENDING): implication creation deterministically 409s
    (LearningCandidateNotValidatedError); the transition then succeeds.

    reverse=True (transition attempted first): the transition succeeds;
    the implication creation, released second, re-reads the now-VALIDATED
    candidate fresh under its own lock and succeeds too — proving it
    never relies on stale pre-lock state (MVP-26 §12)."""
    campaign_id, candidate_public_id, _workspace_id = _pending_candidate(postgres_engine)
    with Session(postgres_engine) as lookup:
        campaign = CampaignRepository(lookup).get_by_public_id(campaign_id)
        candidate = LearningService(lookup).candidates.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=candidate_public_id
        )
        cid = candidate.id

    operations = ["implication", "validate"]
    if reverse:
        operations.reverse()

    with Session(postgres_engine) as check:
        before = check.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.learning_candidate_id == cid))

    ready = Event()
    second_pid: list[int] = []
    with Session(postgres_engine, expire_on_commit=False) as first, ThreadPoolExecutor(max_workers=1) as pool:
        first.scalar(select(LearningCandidate).where(LearningCandidate.id == cid).with_for_update())
        first_pid = first.scalar(text("select pg_backend_pid()"))

        def second():
            with Session(postgres_engine, expire_on_commit=False) as s:
                second_pid.append(s.scalar(text("select pg_backend_pid()")))
                ready.set()
                return _outcome(s, cid, operations[1])

        task = pool.submit(second)
        try:
            assert ready.wait(10)
            assert second_pid[0] != first_pid
            deadline = monotonic() + 10
            blocked = False
            with postgres_engine.connect() as observer:
                while monotonic() < deadline:
                    blockers = observer.execute(text("select pg_blocking_pids(:pid)"), {"pid": second_pid[0]}).scalar_one()
                    observer.commit()
                    if first_pid in blockers:
                        blocked = True
                        break
                    sleep(0.01)
            assert blocked, "Second operation never demonstrated a PostgreSQL lock wait"
            first_result = _outcome(first, cid, operations[0])
        finally:
            first.rollback()  # release even on assertion failure
        second_result = task.result(timeout=15)

    results = [first_result, second_result]
    expected = ["conflict", "ok"] if not reverse else ["ok", "ok"]
    assert results == expected

    with Session(postgres_engine) as s:
        candidate = s.get(LearningCandidate, cid)
        assert candidate.status is LearningCandidateStatus.VALIDATED
        implications = list(s.scalars(select(StrategicImplication).where(StrategicImplication.learning_candidate_id == cid)))
        assert len(implications) == (0 if not reverse else 1)
        after = s.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.learning_candidate_id == cid))
        assert after - before == results.count("ok")


def test_concurrent_implication_creates_for_same_candidate_both_succeed(postgres_engine):
    """MVP-26 §7/§55: cardinality is deliberately 0..N, so two simultaneous
    StrategicImplication creations for the same VALIDATED candidate must
    BOTH succeed as two distinct rows, serialized safely (no lost row, no
    corruption) through the same canonical LearningCandidate lock that
    also guards status/qualification re-evaluation."""
    campaign_id, candidate_public_id, _workspace_id = _candidate_at(postgres_engine, LearningCandidateStatus.VALIDATED)
    created_ids: list[str] = []
    ids_lock = threading.Lock()

    def operation(session: Session, which: int) -> None:
        campaign = CampaignRepository(session).get_by_public_id(campaign_id)
        service = LearningService(session)
        candidate = service.candidates.get_for_campaign_by_public_id(campaign_id=campaign.id, public_id=candidate_public_id)
        implication = service.record_strategic_implication(learning_candidate=candidate, statement=f"Concurrent angle {which}.")
        with ids_lock:
            created_ids.append(implication.public_id)

    outcomes, _errors, backend_pids = _run_two(postgres_engine, operation)
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
    assert outcomes.count("ok") == 2, outcomes
    assert len(created_ids) == 2 and len(set(created_ids)) == 2

    with Session(postgres_engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(campaign_id)
        candidate = LearningService(session).candidates.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=candidate_public_id
        )
        implications = list(session.scalars(select(StrategicImplication).where(StrategicImplication.learning_candidate_id == candidate.id)))
        assert len(implications) == 2
        assert {i.public_id for i in implications} == set(created_ids)
