"""Forced real PostgreSQL lock orderings, including stale identity-map reads.

The second connection must be observed blocked by PostgreSQL before the first
operation runs. No random winner, sleep-based order, or mocked database lock.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

from app.core.ids import generate_public_id
import pytest
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session
from app.learning.models import LearningCandidate, LearningCandidateStatus as Status, LearningQualificationSignal, ReplicationStatus as Replication, EvidenceRelationship as Relation, EvidenceRemovalReason as Removal
from app.learning.qualification import QualificationService, sufficiency_errors, effective
from app.learning.service import LearningService
from app.campaigns.models import Campaign
from app.measurement.models import PerformanceSignal
from app.audit.models import AuditEvent
from app.core.api_errors import ApiError
from tests.learningtest import build_learning_candidate, seed_sufficient_qualification
from tests.contenttest import make_user

pytestmark = pytest.mark.postgres


def setup(engine, pair):
    with Session(engine, expire_on_commit=False) as s:
        c, _, candidate = build_learning_candidate(s)
        seed_sufficient_qualification(s, candidate)
        user = make_user(s); s.commit()
        service = LearningService(s)
        service.transition_learning_candidate(learning_candidate=candidate, target_status=Status.PROVISIONAL)
        service.transition_learning_candidate(learning_candidate=candidate, target_status=Status.VALIDATION_PENDING)
        signals = [PerformanceSignal(public_id=generate_public_id("SIG"), workspace_id=c.workspace_id, campaign_id=c.id, summary=f"Additional observation {n}") for n in range(2)]
        s.add_all(signals); s.commit()
        qservice = QualificationService(s)
        if pair == "dispose":
            qservice.mutate(campaign=c, candidate_public_id=candidate.public_id, actor_user_id=user.id, operation="attach", values={"performance_signal_id": signals[0].public_id, "relationship": Relation.CONTRADICTING, "note": "Failed attempt"})
        if pair in ("replication", "coherence"):
            qservice.mutate(campaign=c, candidate_public_id=candidate.public_id, actor_user_id=user.id, operation="attach", values={"performance_signal_id": signals[0].public_id, "relationship": Relation.SUPPORTING})
        return c.id, candidate.id, candidate.public_id, user.id, signals[1 if pair == "coherence" else 0].public_id


def perform(s, context, operation):
    campaign_id, cid, public_id, uid, signal_id = context
    c = s.get(Campaign, campaign_id)
    if operation == "validate":
        LearningService(s).transition_learning_candidate(learning_candidate=s.get(LearningCandidate, cid), target_status=Status.VALIDATED, actor_user_id=uid)
        return
    values = {
        "attach": {"performance_signal_id": signal_id, "relationship": Relation.CONTRADICTING, "note": "Contradictory observation"},
        "duplicate": {"performance_signal_id": signal_id, "relationship": Relation.SUPPORTING},
        "dispose": {"removal_reason": Removal.ATTACHMENT_ERROR, "removal_note": "Wrong attachment"},
        "qualification": {"scope": " "},
        "replication": {"replication_status": Replication.REPLICATION_EVIDENCE_PRESENT},
    }[operation]
    QualificationService(s).mutate(campaign=c, candidate_public_id=public_id, actor_user_id=uid,
        operation="attach" if operation in ("attach", "duplicate") else "dispose" if operation == "dispose" else "update",
        values=values, signal_public_id=signal_id)


def outcome(s, context, operation):
    try:
        perform(s, context, operation)
        return "ok"
    except ApiError as exc:
        s.rollback()
        assert exc.status_code == 409
        return "conflict"


@pytest.mark.parametrize("pair", ["attach", "dispose", "qualification", "replication", "duplicate", "coherence"])
@pytest.mark.parametrize("reverse", [False, True])
def test_forced_orderings(postgres_engine, pair, reverse):
    context = setup(postgres_engine, pair)
    operations = [pair, "validate"]
    if pair == "duplicate": operations = ["duplicate", "duplicate"]
    if pair == "coherence": operations = ["attach", "replication"]
    if reverse: operations.reverse()
    cid = context[1]
    with Session(postgres_engine) as check:
        before = check.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.learning_candidate_id == cid))
    ready = Event(); second_pid = []
    with Session(postgres_engine, expire_on_commit=False) as first, ThreadPoolExecutor(max_workers=1) as pool:
        first.scalar(select(LearningCandidate).where(LearningCandidate.id == cid).with_for_update())
        first_pid = first.scalar(text("select pg_backend_pid()"))
        def second():
            with Session(postgres_engine, expire_on_commit=False) as s:
                stale_candidate = s.get(LearningCandidate, cid)
                # Keep strong references so populate_existing is actually exercised.
                stale_q, stale_rows = QualificationService(s).read(stale_candidate)
                second_pid.append(s.scalar(text("select pg_backend_pid()")))
                ready.set()
                result = outcome(s, context, operations[1])
                assert stale_q is not None and isinstance(stale_rows, list)
                return result
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
                        blocked = True; break
                    sleep(0.01)
            assert blocked, "Second operation never demonstrated a PostgreSQL lock wait"
            first_result = outcome(first, context, operations[0])
        finally:
            first.rollback()  # release even on assertion failure
        second_result = task.result(timeout=15)
    results = [first_result, second_result]
    expected = {
        "attach": ["ok", "conflict"],
        "dispose": ["ok", "ok"] if not reverse else ["conflict", "ok"],
        "qualification": ["ok", "conflict"],
        "replication": ["ok", "ok"] if not reverse else ["ok", "conflict"],
        "duplicate": ["ok", "conflict"],
        "coherence": ["ok", "conflict"],
    }[pair]
    assert results == expected
    with Session(postgres_engine) as s:
        candidate = s.get(LearningCandidate, cid)
        q, rows = QualificationService(s).read(candidate)
        assert q is not None
        assert len({r.performance_signal_id for r in rows if effective(r)}) == len([r for r in rows if effective(r)])
        after = s.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.learning_candidate_id == cid))
        assert after - before == results.count("ok")
        if candidate.status == Status.VALIDATED:
            assert not sufficiency_errors(q, rows)
        if pair == "attach":
            assert candidate.status == (Status.VALIDATED if reverse else Status.VALIDATION_PENDING)
            assert len(rows) == (0 if reverse else 1)
        if pair == "dispose":
            assert len(rows) == 1 and rows[0].removal_reason == Removal.ATTACHMENT_ERROR
            assert candidate.status == (Status.VALIDATION_PENDING if reverse else Status.VALIDATED)
        if pair == "qualification":
            assert q.scope == ("Observed campaign and period only" if reverse else " ")
        if pair == "replication":
            assert candidate.status == Status.VALIDATED
            assert q.replication_status == (Replication.REPLICATION_NOT_ESTABLISHED if reverse else Replication.REPLICATION_EVIDENCE_PRESENT)
        if pair == "duplicate": assert len(rows) == 1
        if pair == "coherence":
            assert len(rows) == (1 if reverse else 2)
            assert q.replication_status == (Replication.REPLICATION_EVIDENCE_PRESENT if reverse else Replication.REPLICATION_NOT_ESTABLISHED)
