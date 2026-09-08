"""BACKEND-06 §29: a real, two-thread proof that
``HumanDecisionRequestRepository.get_by_public_id(..., for_update=True)``
actually takes a row lock on PostgreSQL — the primitive
``OrchestrationService.respond_to_decision`` relies on to prevent two
concurrent responses to the same decision from both observing OPEN and
both proceeding. Uses two independent connections against the real test
database (not the rollback-isolated `db_session` fixture, which is a
single connection and cannot demonstrate cross-connection blocking).
"""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy.orm import Session as OrmSession

from app.campaigns.models import CampaignRunStatus
from app.campaigns.repository import CampaignRepository, CampaignRunRepository
from app.orchestration.repository import HumanDecisionRequestRepository
from app.orchestration.service import OrchestrationService
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository

pytestmark = pytest.mark.postgres


def test_concurrent_row_lock_serializes_two_readers(postgres_engine) -> None:
    # `db_session` (used elsewhere in this suite) only releases a
    # SAVEPOINT within a still-open outer transaction that never becomes
    # visible to another connection (see tests/dbtest.py) — the two
    # threads below use genuinely separate connections, so setup here
    # uses a plain session bound to its own fresh connection (no
    # pre-started external transaction), exactly like `get_db()` does in
    # the running app, so each `session.commit()` inside the service
    # really commits, immediately visible to other connections.
    with postgres_engine.connect() as connection:
        session = OrmSession(bind=connection)
        organization = OrganizationRepository(session).create(name="Concurrency Org")
        workspace = WorkspaceRepository(session).create(organization_id=organization.id, name="Concurrency WS")
        campaign = CampaignRepository(session).create(workspace_id=workspace.id, name="Concurrency Campaign")
        run = CampaignRunRepository(session).create(campaign=campaign, run_number=1)
        session.commit()
        service = OrchestrationService(session)
        service._transition_run(
            run=run, target=CampaignRunStatus.RUNNING, campaign_id=campaign.id, actor_user_id=None, request_id=None
        )
        session.commit()
        request = service.create_decision_request(
            campaign=campaign, run=run, question="?", stage_execution_id=None, actor_user_id=None, request_id=None
        )
        request_public_id = request.public_id
        session.close()

    lock_acquired = threading.Event()
    release_lock = threading.Event()
    second_reader_started_at: list[float] = []
    second_reader_finished_at: list[float] = []

    def hold_the_lock() -> None:
        with postgres_engine.connect() as connection:
            with connection.begin():
                session = OrmSession(bind=connection)
                HumanDecisionRequestRepository(session).get_by_public_id(request_public_id, for_update=True)
                lock_acquired.set()
                release_lock.wait(timeout=5)
                # transaction commits (releasing the lock) on context exit

    def contend_for_the_lock() -> None:
        lock_acquired.wait(timeout=5)
        second_reader_started_at.append(time.monotonic())
        with postgres_engine.connect() as connection:
            with connection.begin():
                session = OrmSession(bind=connection)
                HumanDecisionRequestRepository(session).get_by_public_id(request_public_id, for_update=True)
                second_reader_finished_at.append(time.monotonic())

    holder = threading.Thread(target=hold_the_lock)
    contender = threading.Thread(target=contend_for_the_lock)

    holder.start()
    lock_acquired.wait(timeout=5)
    contender.start()
    time.sleep(0.3)  # give the contender a real chance to block on the row lock
    release_lock.set()
    holder.join(timeout=5)
    contender.join(timeout=5)

    assert second_reader_finished_at, "the contending thread never completed"
    blocked_duration = second_reader_finished_at[0] - second_reader_started_at[0]
    assert blocked_duration >= 0.25, (
        f"expected the second reader to block for roughly the 0.3s hold, "
        f"observed only {blocked_duration:.3f}s — the row lock may not be effective"
    )
