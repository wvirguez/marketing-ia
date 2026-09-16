"""Real PostgreSQL two-connection races through the production
MeasurementService distribution-evidence methods (MVP-19B §71/§72;
MVP-21A-R1/MVP-21B §44 for the summary-vs-correction race)."""

from __future__ import annotations

import threading
from datetime import date, timedelta, timezone
from datetime import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.campaigns.models import Campaign
from app.content.models import ContentApprovalStatus, ContentDistribution
from app.content.service import ContentService
from app.core.api_errors import EvidenceCorrectionTargetStaleError
from app.measurement.models import DistributionMetricEvidence, MetricEntry
from app.measurement.service import MeasurementService
from tests.contenttest import build_plan_with_item, default_piece_fields, default_version_payload, make_user
from tests.measurementtest import next_client_request_id

pytestmark = pytest.mark.postgres


def _today() -> date:
    return dt.now(timezone.utc).date()


def _distributed_piece(engine):
    with Session(engine) as session:
        campaign, _run, _stages, plan, item = build_plan_with_item(session)
        user = make_user(session)
        service = ContentService(session)
        brief = service.record_brief(plan_item=item, content_plan=plan, brief="Evidence race")
        piece, _version = service.record_piece(content_brief=brief, initial_payload=default_version_payload(), **default_piece_fields())
        for transition in (service.mark_in_production, service.mark_produced, service.mark_ready_for_review):
            transition(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id, actor_user_id=user.id)
        approval = service.request_approval(
            workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id, actor_user_id=user.id
        )
        service.mark_under_review(workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id, actor_user_id=user.id)
        service.record_authorized_approval_decision(
            workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id,
            decision=ContentApprovalStatus.APPROVED, actor_user_id=user.id,
        )
        service.mark_ready_for_distribution(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id, actor_user_id=user.id)
        service.record_distributed(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id, actor_user_id=user.id)
        distribution = session.execute(select(ContentDistribution).where(ContentDistribution.content_piece_id == piece.id)).scalar_one()
        return campaign.id, distribution.id, user.id


def _connect_barrier_pair(engine, count=2):
    backend_pids: list[int] = []
    pid_lock = threading.Lock()
    connections = []

    def verify_distinct_connections():
        assert len(backend_pids) == count and len(set(backend_pids)) == count, backend_pids

    barrier = threading.Barrier(count, action=verify_distinct_connections)

    def open_one():
        connection = engine.connect()
        pid = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
        connection.commit()
        with pid_lock:
            backend_pids.append(pid)
        connections.append(connection)

    threads = [threading.Thread(target=open_one) for _ in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert len(connections) == count
    return connections, barrier


def test_duplicate_create_race_converges_without_duplication(postgres_engine):
    campaign_id, distribution_id, user_id = _distributed_piece(postgres_engine)
    shared_key = next_client_request_id()
    today = _today()

    connections, barrier = _connect_barrier_pair(postgres_engine)
    outcomes: list[tuple[str, str]] = []
    errors: list[BaseException] = []
    outcomes_lock = threading.Lock()

    def run(connection):
        try:
            with Session(bind=connection) as session:
                campaign = session.get(Campaign, campaign_id)
                distribution = session.get(ContentDistribution, distribution_id)
                service = MeasurementService(session)
                barrier.wait(timeout=10)
                evidence, created = service.create_distribution_evidence(
                    distribution=distribution, campaign=campaign,
                    period_start=today - timedelta(days=3), period_end=today,
                    metric_values={"reach": Decimal("500")}, client_request_id=shared_key,
                    source_reference=None, actor_user_id=user_id,
                )
                with outcomes_lock:
                    outcomes.append(("created" if created else "replay", evidence.public_id))
        except BaseException as exc:  # pragma: no cover - failure path asserted below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(c,)) for c in connections]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors, errors
    assert len(outcomes) == 2
    assert outcomes[0][1] == outcomes[1][1], "[RACE] both callers must converge on exactly one Evidence id"
    assert {o[0] for o in outcomes} <= {"created", "replay"}
    assert any(o[0] == "created" for o in outcomes), "[RACE] exactly one caller must have created the row"

    with Session(postgres_engine) as session:
        rows = session.execute(
            select(DistributionMetricEvidence).where(DistributionMetricEvidence.distribution_id == distribution_id)
        ).scalars().all()
        assert len(rows) == 1, "[RACE] no duplicate Evidence row must exist"
        entry_count = session.execute(
            select(MetricEntry).where(MetricEntry.campaign_id == campaign_id)
        ).scalars().all()
        assert len(entry_count) == 1, "[RACE] no duplicate MetricEntry row must exist"


def test_correction_race_single_successor_no_branching(postgres_engine):
    campaign_id, distribution_id, user_id = _distributed_piece(postgres_engine)
    today = _today()

    with Session(postgres_engine) as session:
        campaign = session.get(Campaign, campaign_id)
        distribution = session.get(ContentDistribution, distribution_id)
        service = MeasurementService(session)
        evidence, _created = service.create_distribution_evidence(
            distribution=distribution, campaign=campaign, period_start=today - timedelta(days=3), period_end=today,
            metric_values={"reach": Decimal("500")}, client_request_id=next_client_request_id(),
            source_reference=None, actor_user_id=user_id,
        )
        target_public_id = evidence.public_id

    connections, barrier = _connect_barrier_pair(postgres_engine)
    outcomes: list[str] = []
    errors: list[BaseException] = []
    outcomes_lock = threading.Lock()

    def run(connection, reason):
        try:
            with Session(bind=connection) as session:
                campaign = session.get(Campaign, campaign_id)
                distribution = session.get(ContentDistribution, distribution_id)
                service = MeasurementService(session)
                barrier.wait(timeout=10)
                try:
                    service.create_distribution_evidence_correction(
                        distribution=distribution, campaign=campaign, target_evidence_public_id=target_public_id,
                        period_start=today - timedelta(days=2), period_end=today,
                        metric_values={"reach": Decimal("600")}, client_request_id=next_client_request_id(),
                        source_reference=None, correction_reason=reason, actor_user_id=user_id,
                    )
                    with outcomes_lock:
                        outcomes.append("ok")
                except EvidenceCorrectionTargetStaleError:
                    with outcomes_lock:
                        outcomes.append("stale")
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(c, f"reason-{i}")) for i, c in enumerate(connections)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors, errors
    assert outcomes.count("ok") == 1 and outcomes.count("stale") == 1, outcomes

    with Session(postgres_engine) as session:
        successors = session.execute(
            select(DistributionMetricEvidence).where(
                DistributionMetricEvidence.distribution_id == distribution_id,
                DistributionMetricEvidence.supersedes_evidence_id.isnot(None),
            )
        ).scalars().all()
        assert len(successors) == 1, "[RACE] exactly one successor must exist — no branching correction chain"


def test_summary_read_vs_correction_never_yields_hybrid_membership(postgres_engine):
    """MVP-21A-R1 §D/§F, MVP-21B §44: the single-statement current-Evidence
    membership query must observe either the coherent pre-commit state
    (predecessor still current) or the coherent post-commit state
    (successor current) for a concurrent correction — never neither, never
    both. The correction's commit is held open (flushed but uncommitted,
    via a ``before_commit`` hook on its own real session/connection) while
    a second, independent connection runs the production summary read —
    genuine two-connection concurrency, not a sequential fabrication."""
    campaign_id, distribution_id, user_id = _distributed_piece(postgres_engine)
    today = _today()

    with Session(postgres_engine) as session:
        campaign = session.get(Campaign, campaign_id)
        distribution = session.get(ContentDistribution, distribution_id)
        service = MeasurementService(session)
        evidence, _created = service.create_distribution_evidence(
            distribution=distribution, campaign=campaign, period_start=today - timedelta(days=10), period_end=today,
            metric_values={"reach": Decimal("100")}, client_request_id=next_client_request_id(),
            source_reference=None, actor_user_id=user_id,
        )
        current_public_id = evidence.public_id

    ITERATIONS = 20
    for i in range(ITERATIONS):
        reader_conn = postgres_engine.connect()
        writer_conn = postgres_engine.connect()
        reader_pid = reader_conn.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
        writer_pid = writer_conn.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
        reader_conn.commit()
        writer_conn.commit()
        assert reader_pid != writer_pid, "[RACE] reader and writer must be genuinely distinct connections"

        ready_evt = threading.Event()
        proceed_evt = threading.Event()
        results: dict = {}
        errors: list[BaseException] = []

        def run_writer(target_public_id=current_public_id, reason=f"race-{i}"):
            try:
                with Session(bind=writer_conn) as writer_session:
                    campaign_local = writer_session.get(Campaign, campaign_id)
                    distribution_local = writer_session.get(ContentDistribution, distribution_id)
                    service_local = MeasurementService(writer_session)

                    def _pause_before_commit(_session):
                        ready_evt.set()
                        proceed_evt.wait(timeout=10)

                    event.listen(writer_session, "before_commit", _pause_before_commit)
                    try:
                        new_evidence, _created = service_local.create_distribution_evidence_correction(
                            distribution=distribution_local, campaign=campaign_local,
                            target_evidence_public_id=target_public_id,
                            period_start=today - timedelta(days=9), period_end=today,
                            metric_values={"reach": Decimal(str(200 + i))},
                            client_request_id=next_client_request_id(), source_reference=None,
                            correction_reason=reason, actor_user_id=user_id,
                        )
                        results["new_public_id"] = new_evidence.public_id
                    finally:
                        event.remove(writer_session, "before_commit", _pause_before_commit)
            except BaseException as exc:  # pragma: no cover - failure path asserted below
                errors.append(exc)

        writer_thread = threading.Thread(target=run_writer)
        writer_thread.start()
        assert ready_evt.wait(timeout=10), "[RACE] writer never reached before_commit"

        # Pre-commit read: the writer's correction is flushed on its own
        # connection/transaction but NOT yet committed — under READ
        # COMMITTED it must be fully invisible here.
        with Session(bind=reader_conn) as reader_session:
            pre_rows = MeasurementService(reader_session).summarize_distribution_evidence(distribution_id=distribution_id)
        pre_current_ids = {row[0].public_id for row in pre_rows}
        assert pre_current_ids == {current_public_id}, (
            f"[RACE] pre-commit summary must show only the predecessor as current, got {pre_current_ids}"
        )

        proceed_evt.set()
        writer_thread.join(timeout=20)
        assert not errors, errors
        assert "new_public_id" in results

        # Post-commit read: a fresh connection/session — the correction is
        # now fully committed and must be the sole current row.
        with Session(postgres_engine) as post_session:
            post_rows = MeasurementService(post_session).summarize_distribution_evidence(distribution_id=distribution_id)
        post_current_ids = {row[0].public_id for row in post_rows}
        assert post_current_ids == {results["new_public_id"]}, (
            f"[RACE] post-commit summary must show only the successor as current, got {post_current_ids}"
        )

        current_public_id = results["new_public_id"]
        reader_conn.close()
        writer_conn.close()
