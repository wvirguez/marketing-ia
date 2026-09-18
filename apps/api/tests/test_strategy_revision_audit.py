"""Audit attribution and atomicity for Governed Strategy Revision (MVP-30B,
frozen MVP-30A/-30A-R1 contract §U/§22). All marked `postgres`."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.audit.models import ActorType, AuditEvent
from app.audit.repository import AuditEventRepository
from app.orchestration.models import StrategyRevision
from app.orchestration.service import EVENT_STRATEGY_REVISION_RECORDED, StrategyRevisionService
from app.strategy.models import Positioning, Strategy
from tests.orchestrationtest import build_base_strategy, build_strategic_approval, build_strategy_revision

pytestmark = pytest.mark.postgres


def _total_count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


# --- exact attribution -------------------------------------------------------


def test_recorded_event_identifies_the_exact_revision_and_actor(db_session) -> None:
    campaign, base_strategy, approval, result_strategy, _positioning, revision, actor = build_strategy_revision(
        db_session
    )
    event = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == EVENT_STRATEGY_REVISION_RECORDED, AuditEvent.strategy_revision_id == revision.id
        )
    )
    assert event is not None
    assert event.workspace_id == result_strategy.workspace_id
    assert event.campaign_id == campaign.id
    assert event.actor_type == ActorType.USER
    assert event.actor_user_id == actor.id
    assert event.new_state == f"v{result_strategy.version}"


def test_actor_user_id_is_mandatory_never_system(db_session) -> None:
    """Unlike Commercial's own lower-weight record-keeping actions,
    recording a governed Strategy Revision is an OWNER/ADMIN-gated,
    high-weight governance action — the most consequential write in this
    entire chain, since it actually mutates the operative Strategy.
    ``actor_user_id`` has no default and is always a real human, never
    ``ActorType.SYSTEM`` (MVP-30A §E)."""
    import inspect

    params = inspect.signature(StrategyRevisionService.revise_strategy).parameters
    assert params["actor_user_id"].default is inspect.Parameter.empty


# --- atomicity: no partially-committed successful revision -------------------


def test_no_partial_revision_survives_a_mid_record_failure(db_session) -> None:
    """MVP-30A-R1 §I/§V: a failure during the audit write must leave no
    committed Strategy, Positioning, or StrategyRevision row — this is
    especially important because "no orphan REVISION-origin Strategy" is
    transactionally guaranteed rather than DB-enforced (see
    ``app/strategy/models.py``'s own "ORIGIN" docstring)."""
    campaign, _recommendation, _decision, approval, actor = build_strategic_approval(db_session)
    base_strategy, _run, _stage = build_base_strategy(db_session, campaign=campaign)
    service = StrategyRevisionService(db_session)

    events_before = _total_count(db_session, AuditEvent)
    revisions_before = _total_count(db_session, StrategyRevision)
    strategies_before = _total_count(db_session, Strategy)
    positionings_before = _total_count(db_session, Positioning)

    def _fail(self, *args, **kwargs):
        raise RuntimeError("simulated mid-record audit failure")

    with patch.object(AuditEventRepository, "record", _fail):
        with pytest.raises(RuntimeError, match="simulated mid-record audit failure"):
            service.revise_strategy(
                campaign=campaign, base_strategy_public_id=base_strategy.public_id,
                strategic_approval_public_id=approval.public_id,
                summary="should not persist", positioning_statement="should not persist",
                actor_user_id=actor.id,
            )

    db_session.rollback()

    assert _total_count(db_session, AuditEvent) == events_before
    assert _total_count(db_session, StrategyRevision) == revisions_before
    assert _total_count(db_session, Strategy) == strategies_before
    assert _total_count(db_session, Positioning) == positionings_before
