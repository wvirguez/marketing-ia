"""Audit attribution and atomicity for Commercial persistence (MVP-27,
MVP-27A-R2 §I/§J/§K). All marked `postgres`."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.audit.models import ActorType, AuditEvent
from app.audit.repository import AuditEventRepository
from app.commercial.models import CommercialObjective, Offer
from app.commercial.service import (
    EVENT_OBJECTIVE_RECORDED,
    EVENT_OBJECTIVE_SUPERSEDED,
    EVENT_OFFER_RECORDED,
    EVENT_OFFER_SUPERSEDED,
    CommercialService,
)
from tests.commercialtest import build_commercial_objective, build_offer

pytestmark = pytest.mark.postgres


def _total_count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


# --- exact attribution -----------------------------------------------------


def test_objective_recorded_event_identifies_the_exact_objective(db_session) -> None:
    campaign, objective = build_commercial_objective(db_session)
    event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_OBJECTIVE_RECORDED, AuditEvent.commercial_objective_id == objective.id)
    )
    assert event is not None
    assert event.workspace_id == objective.workspace_id
    assert event.actor_type == ActorType.SYSTEM  # no actor_user_id supplied by the fixture helper
    assert event.offer_id is None


def test_offer_recorded_event_identifies_the_exact_offer(db_session) -> None:
    campaign, offer = build_offer(db_session)
    event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_OFFER_RECORDED, AuditEvent.offer_id == offer.id)
    )
    assert event is not None
    assert event.workspace_id == offer.workspace_id
    assert event.commercial_objective_id is None


def test_supersession_emits_both_events_in_the_same_request_correlation(db_session) -> None:
    campaign, original = build_commercial_objective(db_session)
    replacement = CommercialService(db_session).supersede_commercial_objective(
        campaign=campaign, objective_public_id=original.public_id, statement="New version.",
        actor_user_id=None, request_id="req-commercial-1",
    )
    recorded = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_OBJECTIVE_RECORDED, AuditEvent.commercial_objective_id == replacement.id)
    )
    superseded = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_OBJECTIVE_SUPERSEDED, AuditEvent.commercial_objective_id == original.id)
    )
    assert recorded is not None and superseded is not None
    assert recorded.request_id == superseded.request_id == "req-commercial-1"
    # Authoritative link lives on the domain row itself, never only in new_state.
    assert original.superseded_by_commercial_objective_id == replacement.id
    # new_state is human-legibility only, mirroring the existing f"v{version}" convention.
    assert recorded.new_state == f"supersedes:{original.public_id}"
    assert superseded.new_state == f"superseded_by:{replacement.public_id}"


def test_offer_supersession_emits_both_events(db_session) -> None:
    campaign, original = build_offer(db_session)
    replacement = CommercialService(db_session).supersede_offer(
        campaign=campaign, offer_public_id=original.public_id, statement="New version.",
    )
    recorded = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_OFFER_RECORDED, AuditEvent.offer_id == replacement.id)
    )
    superseded = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_OFFER_SUPERSEDED, AuditEvent.offer_id == original.id)
    )
    assert recorded is not None and superseded is not None
    assert original.superseded_by_offer_id == replacement.id


# --- atomicity: no partially-audited successful supersession -------------


def test_no_partial_audit_or_replacement_survives_a_mid_supersession_failure(db_session) -> None:
    """A failure recording the *second* (superseded) audit event must
    roll back the already-flushed replacement row and the first
    (recorded) audit event too — mirrors
    ``tests/test_planning_audit.py::test_no_partial_audit_set_survives_a_mid_loop_failure``
    exactly."""
    campaign, original = build_commercial_objective(db_session)
    service = CommercialService(db_session)
    events_before = _total_count(db_session, AuditEvent)
    objectives_before = _total_count(db_session, CommercialObjective)

    real_record = AuditEventRepository.record
    call_count = {"n": 0}

    def _fail_on_second_call(self, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:  # replacement "recorded" event (1) succeeds, original "superseded" event (2) fails
            raise RuntimeError("simulated mid-supersession audit failure")
        return real_record(self, *args, **kwargs)

    with patch.object(AuditEventRepository, "record", _fail_on_second_call):
        with pytest.raises(RuntimeError, match="simulated mid-supersession audit failure"):
            service.supersede_commercial_objective(
                campaign=campaign, objective_public_id=original.public_id, statement="should not persist"
            )

    db_session.rollback()

    assert _total_count(db_session, AuditEvent) == events_before
    assert _total_count(db_session, CommercialObjective) == objectives_before
    db_session.refresh(original)
    assert original.superseded_at is None
    assert original.superseded_by_commercial_objective_id is None


def test_no_partial_audit_or_replacement_survives_a_mid_offer_supersession_failure(db_session) -> None:
    campaign, original = build_offer(db_session)
    service = CommercialService(db_session)
    events_before = _total_count(db_session, AuditEvent)
    offers_before = _total_count(db_session, Offer)

    real_record = AuditEventRepository.record
    call_count = {"n": 0}

    def _fail_on_second_call(self, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated mid-supersession audit failure")
        return real_record(self, *args, **kwargs)

    with patch.object(AuditEventRepository, "record", _fail_on_second_call):
        with pytest.raises(RuntimeError, match="simulated mid-supersession audit failure"):
            service.supersede_offer(campaign=campaign, offer_public_id=original.public_id, statement="should not persist")

    db_session.rollback()

    assert _total_count(db_session, AuditEvent) == events_before
    assert _total_count(db_session, Offer) == offers_before
    db_session.refresh(original)
    assert original.superseded_at is None
    assert original.superseded_by_offer_id is None
