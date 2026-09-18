"""Audit attribution and atomicity for Governed Next-Cycle Hypothesis
creation (MVP-31B, frozen MVP-31A/-31A-R1 contract). All marked
`postgres`."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.audit.models import ActorType, AuditEvent
from app.audit.repository import AuditEventRepository
from app.strategy.models import Hypothesis, HypothesisStatus
from app.strategy.service import EVENT_HYPOTHESIS_RECORDED, StrategyService
from tests.orchestrationtest import build_base_strategy, build_strategic_approval

pytestmark = pytest.mark.postgres


def _total_count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


# --- exact attribution -------------------------------------------------------


def test_recorded_event_identifies_the_exact_hypothesis_and_actor(db_session) -> None:
    campaign, _recommendation, _decision, _approval, actor = build_strategic_approval(db_session)
    base_strategy, _run, _stage = build_base_strategy(db_session, campaign=campaign)

    hypothesis = StrategyService(db_session).create_hypothesis(
        campaign=campaign, strategy_public_id=base_strategy.public_id, statement="x", actor_user_id=actor.id,
    )

    event = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == EVENT_HYPOTHESIS_RECORDED, AuditEvent.hypothesis_id == hypothesis.id
        )
    )
    assert event is not None
    assert event.workspace_id == base_strategy.workspace_id
    assert event.campaign_id == campaign.id
    assert event.strategy_id == base_strategy.id
    assert event.actor_type == ActorType.USER
    assert event.actor_user_id == actor.id
    assert event.new_state == HypothesisStatus.OPEN.value


def test_bootstrap_and_governed_creation_share_the_same_event_namespace(db_session) -> None:
    """MVP-31A-R1 §15: no split event namespace — bootstrap-created and
    governed-created Hypotheses are the exact same domain entity, so both
    record ``EVENT_HYPOTHESIS_RECORDED``."""
    from tests.strategytest import default_hypothesis
    from tests.researchtest import build_campaign_run_with_stages
    from app.orchestration.models import BusinessStage

    campaign, run, stages = build_campaign_run_with_stages(db_session, campaign_name="Audit Namespace Campaign")
    StrategyService(db_session).record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.", hypotheses=[default_hypothesis()],
    )
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_HYPOTHESIS_RECORDED, AuditEvent.campaign_id == campaign.id)
    ).scalars().all()
    assert len(events) == 1  # the bootstrap-created Hypothesis's own event, same event_type


def test_actor_user_id_is_mandatory_never_system(db_session) -> None:
    """MVP-31A §U: unlike bootstrap (which reuses SYSTEM as a fallback if
    no actor was threaded through), the governed HTTP-reachable path
    always has a real authenticated human — no default."""
    import inspect

    params = inspect.signature(StrategyService.create_hypothesis).parameters
    assert params["actor_user_id"].default is inspect.Parameter.empty


# --- atomicity: no partially-committed successful creation -------------------


def test_no_partial_hypothesis_survives_a_mid_record_failure(db_session) -> None:
    campaign, _recommendation, _decision, _approval, actor = build_strategic_approval(db_session)
    base_strategy, _run, _stage = build_base_strategy(db_session, campaign=campaign)
    service = StrategyService(db_session)

    events_before = _total_count(db_session, AuditEvent)
    hypotheses_before = _total_count(db_session, Hypothesis)

    def _fail(self, *args, **kwargs):
        raise RuntimeError("simulated mid-record audit failure")

    with patch.object(AuditEventRepository, "record", _fail):
        with pytest.raises(RuntimeError, match="simulated mid-record audit failure"):
            service.create_hypothesis(
                campaign=campaign, strategy_public_id=base_strategy.public_id,
                statement="should not persist", actor_user_id=actor.id,
            )

    db_session.rollback()

    assert _total_count(db_session, AuditEvent) == events_before
    assert _total_count(db_session, Hypothesis) == hypotheses_before
