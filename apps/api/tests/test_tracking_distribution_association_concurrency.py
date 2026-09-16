"""Real PostgreSQL two-connection races for MVP-24 (ContentDistribution
<-> TrackingRequirement Association) — proves the load-bearing invariant:
NO association can be newly created after the Distribution has committed
as DISTRIBUTED, and no association can be removed after that point
either. Both operations serialize through the SAME row lock
``record_distributed`` already takes — the owning ContentPiece — not a
lock placed directly on ContentDistribution, matching MVP-24B's own
design rationale (§V of the implementation report).
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.content.models import ContentApprovalStatus, ContentDistribution, ContentDistributionStatus, ContentDistributionTrackingRequirement, ContentPiece
from app.content.service import ContentService
from app.core.api_errors import InvalidLifecycleTransitionError, TrackingRequirementAlreadyAssociatedError
from app.tracking.service import TrackingService
from tests.contenttest import build_plan_with_item, default_piece_fields, default_version_payload, make_user

pytestmark = pytest.mark.postgres


def _ready_distribution_with_requirement(engine):
    with Session(engine) as session:
        campaign, _run, _stages, plan, item = build_plan_with_item(session)
        user = make_user(session)
        content_service = ContentService(session)
        brief = content_service.record_brief(plan_item=item, content_plan=plan, brief="Association race")
        piece, _version = content_service.record_piece(
            content_brief=brief, initial_payload=default_version_payload(), **default_piece_fields()
        )
        for transition in (content_service.mark_in_production, content_service.mark_produced, content_service.mark_ready_for_review):
            transition(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id, actor_user_id=user.id)
        approval = content_service.request_approval(
            workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id, actor_user_id=user.id
        )
        content_service.mark_under_review(workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id, actor_user_id=user.id)
        content_service.record_authorized_approval_decision(
            workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id,
            decision=ContentApprovalStatus.APPROVED, actor_user_id=user.id,
        )
        content_service.mark_ready_for_distribution(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id, actor_user_id=user.id)

        tracking_service = TrackingService(session)
        tracking_plan = tracking_service.record_tracking_plan(campaign=campaign)
        requirement = tracking_service.record_tracking_requirement(tracking_plan=tracking_plan, name="Purchase event")

        return piece.public_id, campaign.workspace_id, user.id, requirement.public_id


def _distinct_connections_race(engine, operation_a, operation_b):
    """Runs ``operation_a``/``operation_b`` on two genuinely separate
    connections, synchronized via a Barrier so both attempt their write
    nearly simultaneously — the ContentPiece row lock, not Python timing,
    is what actually serializes them."""
    outcomes = []
    errors = []
    backend_pids = []
    pid_lock = threading.Lock()

    def verify_distinct_connections():
        assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1], backend_pids

    barrier = threading.Barrier(2, action=verify_distinct_connections)

    def run(operation, label):
        try:
            with engine.connect() as connection:
                pid = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
                connection.commit()
                with pid_lock:
                    backend_pids.append(pid)
                with Session(bind=connection) as session:
                    barrier.wait(timeout=10)
                    try:
                        operation(session)
                        outcomes.append((label, "ok"))
                    except (InvalidLifecycleTransitionError, TrackingRequirementAlreadyAssociatedError) as exc:
                        outcomes.append((label, type(exc).__name__))
        except BaseException as exc:  # noqa: BLE001 - surfaced via assertion below
            errors.append((label, exc))

    threads = [threading.Thread(target=run, args=(operation_a, "A")), threading.Thread(target=run, args=(operation_b, "B"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert not errors, errors
    assert len(set(backend_pids)) == 2, backend_pids
    assert all(not t.is_alive() for t in threads)
    return dict(outcomes)


def test_associate_vs_distributed_race_ordering_1_association_first(postgres_engine):
    """Ordering 1: association wins the lock first, commits; record_distributed
    then proceeds and succeeds. Final: DISTRIBUTED, association exists."""
    piece_id, workspace_id, user_id, requirement_id = _ready_distribution_with_requirement(postgres_engine)

    def op_associate(session: Session) -> None:
        service = ContentService(session)
        requirement = TrackingService(session).requirements.get_for_campaign_by_public_id(
            campaign_id=_campaign_id_for_piece(session, piece_id), public_id=requirement_id,
        )
        service.associate_tracking_requirement(
            workspace_id=workspace_id, content_piece_public_id=piece_id, tracking_requirement=requirement, actor_user_id=user_id,
        )

    def op_distribute(session: Session) -> None:
        ContentService(session).record_distributed(workspace_id=workspace_id, content_piece_public_id=piece_id, actor_user_id=user_id)

    # Run sequentially-but-real-race a handful of times to sample both
    # possible lock-acquisition orders under genuine PostgreSQL contention.
    for _ in range(10):
        piece_id, workspace_id, user_id, requirement_id = _ready_distribution_with_requirement(postgres_engine)
        outcomes = _distinct_connections_race(postgres_engine, op_associate, op_distribute)
        assert outcomes["B"] == "ok"  # record_distributed always legal (no coupling to association)
        with Session(postgres_engine) as session:
            piece = session.execute(select(ContentPiece).where(ContentPiece.public_id == piece_id)).scalar_one()
            distribution = session.execute(select(ContentDistribution).where(ContentDistribution.content_piece_id == piece.id)).scalar_one()
            assert distribution.status is ContentDistributionStatus.DISTRIBUTED
            association_count = session.execute(
                select(ContentDistributionTrackingRequirement).where(ContentDistributionTrackingRequirement.content_distribution_id == distribution.id)
            ).scalars().all()
            if outcomes["A"] == "ok":
                assert len(association_count) == 1
            else:
                assert outcomes["A"] == "InvalidLifecycleTransitionError"
                assert len(association_count) == 0
            # LOAD-BEARING INVARIANT: no association can exist unless the
            # associate call itself reported success.
            assert (len(association_count) == 1) == (outcomes["A"] == "ok")


def test_dissociate_vs_distributed_race(postgres_engine):
    """An existing association raced against record_distributed: if
    dissociate wins the lock, the association is removed then DISTRIBUTED
    proceeds; if record_distributed wins, DISTRIBUTED commits first and
    the dissociate attempt deterministically fails, leaving the
    association intact. No hybrid state in either case."""
    for _ in range(10):
        piece_id, workspace_id, user_id, requirement_id = _ready_distribution_with_requirement(postgres_engine)
        with Session(postgres_engine) as session:
            service = ContentService(session)
            requirement = TrackingService(session).requirements.get_for_campaign_by_public_id(
                campaign_id=_campaign_id_for_piece(session, piece_id), public_id=requirement_id,
            )
            service.associate_tracking_requirement(
                workspace_id=workspace_id, content_piece_public_id=piece_id, tracking_requirement=requirement, actor_user_id=user_id,
            )

        def op_dissociate(session: Session) -> None:
            service = ContentService(session)
            requirement = TrackingService(session).requirements.get_for_campaign_by_public_id(
                campaign_id=_campaign_id_for_piece(session, piece_id), public_id=requirement_id,
            )
            service.dissociate_tracking_requirement(
                workspace_id=workspace_id, content_piece_public_id=piece_id, tracking_requirement=requirement, actor_user_id=user_id,
            )

        def op_distribute(session: Session) -> None:
            ContentService(session).record_distributed(workspace_id=workspace_id, content_piece_public_id=piece_id, actor_user_id=user_id)

        outcomes = _distinct_connections_race(postgres_engine, op_dissociate, op_distribute)
        assert outcomes["B"] == "ok"

        with Session(postgres_engine) as session:
            piece = session.execute(select(ContentPiece).where(ContentPiece.public_id == piece_id)).scalar_one()
            distribution = session.execute(select(ContentDistribution).where(ContentDistribution.content_piece_id == piece.id)).scalar_one()
            assert distribution.status is ContentDistributionStatus.DISTRIBUTED
            remaining = session.execute(
                select(ContentDistributionTrackingRequirement).where(ContentDistributionTrackingRequirement.content_distribution_id == distribution.id)
            ).scalars().all()
            if outcomes["A"] == "ok":
                assert len(remaining) == 0  # dissociate won: removed before DISTRIBUTED
            else:
                assert outcomes["A"] == "InvalidLifecycleTransitionError"
                assert len(remaining) == 1  # record_distributed won: association survives, frozen


def test_duplicate_association_race(postgres_engine):
    """Two genuinely concurrent identical-pair association attempts:
    exactly one succeeds, the other deterministically conflicts on the
    UNIQUE(content_distribution_id, tracking_requirement_id) constraint —
    never two rows, never a raw IntegrityError leak."""
    for _ in range(10):
        piece_id, workspace_id, user_id, requirement_id = _ready_distribution_with_requirement(postgres_engine)

        def op_associate(session: Session) -> None:
            service = ContentService(session)
            requirement = TrackingService(session).requirements.get_for_campaign_by_public_id(
                campaign_id=_campaign_id_for_piece(session, piece_id), public_id=requirement_id,
            )
            service.associate_tracking_requirement(
                workspace_id=workspace_id, content_piece_public_id=piece_id, tracking_requirement=requirement, actor_user_id=user_id,
            )

        outcomes = _distinct_connections_race(postgres_engine, op_associate, op_associate)
        assert sorted(outcomes.values()) == ["TrackingRequirementAlreadyAssociatedError", "ok"]

        with Session(postgres_engine) as session:
            piece = session.execute(select(ContentPiece).where(ContentPiece.public_id == piece_id)).scalar_one()
            distribution = session.execute(select(ContentDistribution).where(ContentDistribution.content_piece_id == piece.id)).scalar_one()
            rows = session.execute(
                select(ContentDistributionTrackingRequirement).where(ContentDistributionTrackingRequirement.content_distribution_id == distribution.id)
            ).scalars().all()
            assert len(rows) == 1
            events = session.execute(
                select(AuditEvent).where(
                    AuditEvent.event_type == "distribution.tracking_requirement.associated",
                    AuditEvent.distribution_id == distribution.id,
                )
            ).scalars().all()
            assert len(events) == 1


def _campaign_id_for_piece(session: Session, piece_public_id: str):
    from app.campaigns.models import Campaign
    from app.content.models import ContentBrief
    from app.planning.models import ContentPlan, PlanItem

    piece = session.execute(select(ContentPiece).where(ContentPiece.public_id == piece_public_id)).scalar_one()
    brief = session.get(ContentBrief, piece.content_brief_id)
    plan_item = session.get(PlanItem, brief.plan_item_id)
    plan = session.get(ContentPlan, plan_item.content_plan_id)
    return plan.campaign_id
