"""Real PostgreSQL concurrency proof for Governed Content Brief creation
(MVP-34B, frozen MVP-34A contract).

The frozen contract intentionally introduces NO new pessimistic lock — the
only concurrency-sensitive operation is the pre-existing ``plan_item_id``
optimistic race, already backed by a real DB UniqueConstraint
(``uq_content_briefs_plan_item_id``) + ``IntegrityError`` ->
``PlanItemAlreadyBriefedError`` catch (``ContentService.record_brief``'s own
production-proven mechanism, reused unmodified by the new governed HTTP
route). This test proves that mechanism genuinely serializes two concurrent
creates against the same PlanItem without inventing a lock-wait assertion
the frozen contract does not call for.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import ActorType, AuditEvent
from app.campaigns.repository import CampaignRepository
from app.content.models import ContentBrief
from app.content.service import ContentService
from app.planning.models import PlanItem
from app.planning.repository import ContentPlanRepository, PlanItemRepository
from app.planning.service import PlanningService
from tests.contenttest import build_plan_with_item, make_user
from tests.test_content_plan_concurrency import _run_two

pytestmark = pytest.mark.postgres


def _create_fresh_plan_item(postgres_engine, *, campaign_public_id: str, actor_id, iteration: int) -> str:
    """Each iteration needs a fresh, unbriefed PlanItem — the frozen 0..1
    cardinality means racing twice against an already-briefed item would
    deterministically produce two conflicts, not the "one ok / one
    conflict" outcome this test is proving."""
    with Session(postgres_engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(campaign_public_id)
        _plan, items = PlanningService(session).create_plan(
            campaign=campaign, summary=f"Iteration {iteration}.", experiment_public_id=None,
            items=[{"format": "Reel", "objective": "x", "sequence": 1, "scheduled_date": None}],
            actor_user_id=actor_id,
        )
        session.commit()
        return items[0].public_id


def _run_iteration(postgres_engine, *, campaign_id, plan_item_public_id: str, actor_id) -> tuple[list[str], list[Exception]]:
    def operation(session: Session, which: int) -> None:
        plan_item = PlanItemRepository(session).get_for_campaign_by_public_id(
            campaign_id=campaign_id, public_id=plan_item_public_id
        )
        content_plan = ContentPlanRepository(session).get_by_id(plan_item.content_plan_id)
        ContentService(session).record_brief(
            plan_item=plan_item, content_plan=content_plan, brief=f"Attempt {which}.", actor_user_id=actor_id,
        )

    outcomes, errors, backend_pids = _run_two(postgres_engine, operation)
    assert not errors
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
    return outcomes, errors


def test_two_concurrent_brief_creates_for_the_same_plan_item_serialize(postgres_engine) -> None:
    with Session(postgres_engine) as setup:
        campaign, _run, _stages, _plan, _plan_item = build_plan_with_item(
            setup, campaign_name="Concurrency Brief Campaign"
        )
        actor = make_user(setup)
        setup.commit()
        campaign_id = campaign.id
        campaign_public_id = campaign.public_id
        actor_id = actor.id

    results = []
    for iteration in range(3):
        plan_item_public_id = _create_fresh_plan_item(
            postgres_engine, campaign_public_id=campaign_public_id, actor_id=actor_id, iteration=iteration
        )

        outcomes, _errors = _run_iteration(
            postgres_engine, campaign_id=campaign_id, plan_item_public_id=plan_item_public_id, actor_id=actor_id
        )
        results.append(outcomes)
        assert outcomes.count("ok") == 1
        assert outcomes.count("conflict") == 1

        with Session(postgres_engine) as check:
            brief_rows = list(
                check.scalars(
                    select(ContentBrief)
                    .join(PlanItem, ContentBrief.plan_item_id == PlanItem.id)
                    .where(PlanItem.public_id == plan_item_public_id)
                )
            )
            assert len(brief_rows) == 1  # exactly one survivor
            events = list(
                check.scalars(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "content.brief.recorded",
                        AuditEvent.content_brief_id == brief_rows[0].id,
                    )
                )
            )
            assert len(events) == 1
            assert events[0].actor_type == ActorType.USER

    print(f"Brief concurrency iteration outcomes: {results}")
