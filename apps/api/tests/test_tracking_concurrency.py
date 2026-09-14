"""MVP-15B concurrency repair: a real, two-thread proof that TrackingPlan
creation races cleanly to a single winner plus a deterministic 409
conflict for the loser, never a raw unhandled ``IntegrityError``. Uses
two independent connections against the real test database (mirrors
``tests/test_settings_concurrency.py``'s established two-thread/barrier
pattern), since the single-connection, rollback-isolated ``db_session``
fixture cannot demonstrate a real uniqueness race.

Unlike the notification-preference precedent (which silently returns the
existing row to the race loser), the frozen Tracking Plan creation
contract is a strict conflict: the loser must receive the exact same
``TrackingPlanAlreadyExistsError`` a sequential duplicate would.
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.audit.models import AuditEvent
from app.campaigns.models import Campaign
from app.campaigns.repository import CampaignRepository
from app.core.api_errors import TrackingPlanAlreadyExistsError
from app.tracking.models import TrackingPlan
from app.tracking.service import TrackingService
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository

pytestmark = pytest.mark.postgres


def test_concurrent_plan_creation_yields_exactly_one_winner_and_a_clean_conflict(postgres_engine) -> None:
    with postgres_engine.connect() as connection:
        session = OrmSession(bind=connection)
        organization = OrganizationRepository(session).create(name="Tracking Race Org")
        workspace = WorkspaceRepository(session).create(organization_id=organization.id, name="Tracking Race WS")
        campaign = CampaignRepository(session).create(workspace_id=workspace.id, name="Tracking Race Campaign")
        session.commit()
        campaign_id = campaign.id
        session.close()

    barrier = threading.Barrier(2)
    results: dict[str, tuple[str, object]] = {}

    def _create(key: str) -> None:
        with postgres_engine.connect() as connection:
            session = OrmSession(bind=connection)
            campaign = session.get(Campaign, campaign_id)
            barrier.wait(timeout=5)
            try:
                plan = TrackingService(session).record_tracking_plan(campaign=campaign)
                results[key] = ("ok", plan.id)
            except TrackingPlanAlreadyExistsError as exc:
                results[key] = ("conflict", exc)

    thread_a = threading.Thread(target=_create, args=("a",))
    thread_b = threading.Thread(target=_create, args=("b",))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=10)
    thread_b.join(timeout=10)

    assert set(results.keys()) == {"a", "b"}, f"a thread never completed: {results}"
    outcomes = {results["a"][0], results["b"][0]}
    assert outcomes == {"ok", "conflict"}, f"expected exactly one winner and one conflict, got {results}"

    with postgres_engine.connect() as connection:
        session = OrmSession(bind=connection)
        plans = session.execute(select(TrackingPlan).where(TrackingPlan.campaign_id == campaign_id)).scalars().all()
        assert len(plans) == 1, "exactly one TrackingPlan must survive the race"

        recorded_events = session.execute(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == "tracking.plan.recorded", AuditEvent.tracking_plan_id == plans[0].id)
        ).scalar_one()
        assert recorded_events == 1, "the race loser must never emit its own tracking.plan.recorded event"
