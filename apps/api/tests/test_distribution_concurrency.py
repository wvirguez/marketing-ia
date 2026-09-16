"""Real PostgreSQL two-connection races through the production ContentService."""

import threading

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.models import ContentApprovalStatus, ContentDistribution, ContentDistributionStatus, ContentPiece, ContentPieceStatus
from app.content.service import ContentService
from app.content.repository import ContentVersionRepository
from app.core.api_errors import InvalidLifecycleTransitionError
from tests.contenttest import build_plan_with_item, default_piece_fields, default_version_payload, make_user

pytestmark = pytest.mark.postgres


def _approved_piece(engine):
    with Session(engine) as session:
        campaign, _run, _stages, plan, item = build_plan_with_item(session)
        user = make_user(session)
        service = ContentService(session)
        brief = service.record_brief(plan_item=item, content_plan=plan, brief="Distribution race")
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
        return piece.public_id, campaign.workspace_id, user.id, approval.content_version_id


def _race(engine, operation, *, public_id, workspace_id, user_id):
    outcomes = []
    errors = []
    backend_pids = []
    pid_lock = threading.Lock()

    def verify_distinct_connections():
        assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1], backend_pids

    barrier = threading.Barrier(2, action=verify_distinct_connections)

    def run():
        try:
            with engine.connect() as connection:
                pid = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
                connection.commit()  # End the probe transaction; retain this physical connection.
                with pid_lock:
                    backend_pids.append(pid)
                with Session(bind=connection) as session:
                    barrier.wait(timeout=10)
                    try:
                        operation(ContentService(session), workspace_id, public_id, user_id)
                        outcomes.append("ok")
                    except InvalidLifecycleTransitionError:
                        outcomes.append("conflict")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1], backend_pids
    assert not errors, errors
    assert outcomes.count("ok") == 1 and outcomes.count("conflict") == 1, outcomes
    assert all(not thread.is_alive() for thread in threads)


def test_mark_ready_serializes_and_freezes_approved_version(postgres_engine):
    public_id, workspace_id, user_id, approved_version_id = _approved_piece(postgres_engine)
    # A test-only, later Version makes independent "latest" selection wrong.
    # No public ContentVersion creator is introduced for this proof.
    with Session(postgres_engine) as session:
        piece = session.execute(select(ContentPiece).where(ContentPiece.public_id == public_id)).scalar_one()
        later = ContentVersionRepository(session).create(
            content_piece=piece, payload=default_version_payload(hook="later, unapproved"),
            created_by_user_id=user_id,
        )
        later_id = later.id
        session.commit()
    assert later_id != approved_version_id
    _race(
        postgres_engine,
        lambda service, ws, pid, actor: service.mark_ready_for_distribution(
            workspace_id=ws, content_piece_public_id=pid, actor_user_id=actor
        ),
        public_id=public_id, workspace_id=workspace_id, user_id=user_id,
    )
    with Session(postgres_engine) as session:
        piece = session.execute(select(ContentPiece).where(ContentPiece.public_id == public_id)).scalar_one()
        rows = session.execute(select(ContentDistribution).where(ContentDistribution.content_piece_id == piece.id)).scalars().all()
        assert len(rows) == 1
        assert piece.status is ContentPieceStatus.READY_FOR_DISTRIBUTION
        assert rows[0].status is ContentDistributionStatus.READY
        assert rows[0].content_version_id == approved_version_id
        assert rows[0].content_version_id != later_id
        assert rows[0].channel == piece.channel


def test_record_distributed_serializes_and_keeps_shadow_consistent(postgres_engine):
    public_id, workspace_id, user_id, approved_version_id = _approved_piece(postgres_engine)
    with Session(postgres_engine) as session:
        ContentService(session).mark_ready_for_distribution(
            workspace_id=workspace_id, content_piece_public_id=public_id, actor_user_id=user_id
        )
    _race(
        postgres_engine,
        lambda service, ws, pid, actor: service.record_distributed(
            workspace_id=ws, content_piece_public_id=pid, actor_user_id=actor
        ),
        public_id=public_id, workspace_id=workspace_id, user_id=user_id,
    )
    with Session(postgres_engine) as session:
        piece = session.execute(select(ContentPiece).where(ContentPiece.public_id == public_id)).scalar_one()
        distribution = session.execute(select(ContentDistribution).where(ContentDistribution.content_piece_id == piece.id)).scalar_one()
        assert piece.status is ContentPieceStatus.DISTRIBUTED
        assert distribution.status is ContentDistributionStatus.DISTRIBUTED
        assert distribution.content_version_id == approved_version_id
        assert distribution.distributed_at is not None
