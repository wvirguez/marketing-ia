"""Audit attribution, atomicity, bootstrap non-creation, and downstream
non-effects for CommercialOutcome (MVP-36, frozen by MVP-36A/-R1). All
marked `postgres`."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.audit.models import ActorType, AuditEvent
from app.audit.repository import AuditEventRepository
from app.commercial.models import CommercialObjective, CommercialOutcome, Offer
from app.commercial.service import EVENT_OUTCOME_CORRECTED, EVENT_OUTCOME_RECORDED, CommercialService
from app.content.models import ContentApproval, ContentDistribution, ContentPiece
from app.learning.models import LearningCandidate
from app.measurement.models import DistributionMetricEvidence, MetricEntry
from app.orchestration.models import StrategicDecision
from app.strategy.models import Experiment, Strategy
from app.tracking.models import TrackingRequirement
from tests.commercialtest import build_commercial_outcome
from tests.contenttest import make_user

pytestmark = pytest.mark.postgres


def _total_count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


# --- exact attribution --------------------------------------------------------


def test_outcome_recorded_event_identifies_the_exact_outcome_and_actor(db_session) -> None:
    campaign, outcome = build_commercial_outcome(db_session)
    event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_OUTCOME_RECORDED, AuditEvent.commercial_outcome_id == outcome.id)
    )
    assert event is not None
    assert event.workspace_id == outcome.workspace_id
    assert event.campaign_id == campaign.id
    assert event.actor_type == ActorType.USER  # MVP-36A §M: USER-only, no SYSTEM producer
    assert event.actor_user_id is not None


def test_outcome_corrected_event_identifies_the_exact_correction(db_session) -> None:
    user = make_user(db_session)
    campaign, original = build_commercial_outcome(db_session, actor_user_id=user.id)
    service = CommercialService(db_session)
    correction, created = service.correct_commercial_outcome(
        campaign=campaign, target_outcome_public_id=original.public_id, outcome_type="purchase",
        quantity=None, monetary_value=None, currency=None,
        occurred_at=datetime(2026, 1, 20, tzinfo=timezone.utc), external_reference=None,
        client_request_id=str(uuid.uuid4()), correction_reason="Fixed.", actor_user_id=user.id,
    )
    assert created is True
    event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_OUTCOME_CORRECTED, AuditEvent.commercial_outcome_id == correction.id)
    )
    assert event is not None
    assert event.actor_type == ActorType.USER


def test_exact_replay_creates_zero_new_audit_event(db_session) -> None:
    user = make_user(db_session)
    campaign, outcome = build_commercial_outcome(db_session, actor_user_id=user.id, client_request_id="replay-key-1")
    events_before = _total_count(db_session, AuditEvent)

    service = CommercialService(db_session)
    _outcome, created = service.record_commercial_outcome(
        campaign=campaign, content_distribution_id=None, outcome_type=outcome.outcome_type,
        quantity=outcome.quantity, monetary_value=outcome.monetary_value, currency=outcome.currency,
        occurred_at=outcome.occurred_at, external_reference=outcome.external_reference,
        client_request_id="replay-key-1", actor_user_id=user.id,
    )
    assert created is False
    assert _total_count(db_session, AuditEvent) == events_before


def test_conflicting_replay_creates_zero_successful_audit_event(db_session) -> None:
    from app.core.api_errors import IdempotencyKeyConflictError

    user = make_user(db_session)
    campaign, outcome = build_commercial_outcome(db_session, actor_user_id=user.id, client_request_id="conflict-key-1")
    events_before = _total_count(db_session, AuditEvent)

    service = CommercialService(db_session)
    with pytest.raises(IdempotencyKeyConflictError):
        service.record_commercial_outcome(
            campaign=campaign, content_distribution_id=None, outcome_type="a materially different type",
            quantity=None, monetary_value=None, currency=None, occurred_at=outcome.occurred_at,
            external_reference=None, client_request_id="conflict-key-1", actor_user_id=user.id,
        )
    assert _total_count(db_session, AuditEvent) == events_before


# --- atomicity: no partial write survives a mid-create/correction failure ----


def test_no_partial_row_survives_a_mid_create_audit_failure(db_session) -> None:
    from app.campaigns.repository import CampaignRepository
    from app.workspaces.repository import OrganizationRepository, WorkspaceRepository

    organization = OrganizationRepository(db_session).create(name="Atomic Org")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="Atomic WS")
    campaign = CampaignRepository(db_session).create(workspace_id=workspace.id, name="Atomic Campaign")
    db_session.flush()
    user = make_user(db_session)
    db_session.commit()

    service = CommercialService(db_session)
    outcomes_before = _total_count(db_session, CommercialOutcome)
    events_before = _total_count(db_session, AuditEvent)

    def _fail(self, *args, **kwargs):
        raise RuntimeError("simulated mid-create audit failure")

    with patch.object(AuditEventRepository, "record", _fail):
        with pytest.raises(RuntimeError, match="simulated mid-create audit failure"):
            service.record_commercial_outcome(
                campaign=campaign, content_distribution_id=None, outcome_type="lead", quantity=None,
                monetary_value=None, currency=None, occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                external_reference=None, client_request_id=str(uuid.uuid4()), actor_user_id=user.id,
            )

    db_session.rollback()
    assert _total_count(db_session, CommercialOutcome) == outcomes_before
    assert _total_count(db_session, AuditEvent) == events_before


def test_no_partial_row_survives_a_mid_correction_audit_failure(db_session) -> None:
    user = make_user(db_session)
    campaign, original = build_commercial_outcome(db_session, actor_user_id=user.id)
    db_session.commit()

    service = CommercialService(db_session)
    outcomes_before = _total_count(db_session, CommercialOutcome)
    events_before = _total_count(db_session, AuditEvent)

    def _fail(self, *args, **kwargs):
        raise RuntimeError("simulated mid-correction audit failure")

    with patch.object(AuditEventRepository, "record", _fail):
        with pytest.raises(RuntimeError, match="simulated mid-correction audit failure"):
            service.correct_commercial_outcome(
                campaign=campaign, target_outcome_public_id=original.public_id, outcome_type="purchase",
                quantity=None, monetary_value=None, currency=None,
                occurred_at=datetime(2026, 1, 5, tzinfo=timezone.utc), external_reference=None,
                client_request_id=str(uuid.uuid4()), correction_reason="x", actor_user_id=user.id,
            )

    db_session.rollback()
    assert _total_count(db_session, CommercialOutcome) == outcomes_before
    assert _total_count(db_session, AuditEvent) == events_before
    db_session.refresh(original)
    assert CommercialService(db_session).get_outcome_successor_id(original.id) is None  # still the tip


# --- bootstrap non-creation ----------------------------------------------------


def test_deterministic_bootstrap_creates_zero_commercial_outcomes(db_session) -> None:
    """MVP-36A §AH: bootstrap must never fabricate a realized business
    event. Runs the real bootstrap end to end and asserts zero
    CommercialOutcome rows exist afterward."""
    from app.campaigns.service import CampaignService
    from app.orchestration.service import OrchestrationService
    from app.workspaces.repository import OrganizationRepository, WorkspaceRepository

    organization = OrganizationRepository(db_session).create(name="Bootstrap Org")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="Bootstrap WS")
    user = make_user(db_session)
    campaign, _brief, run = CampaignService(db_session).create_campaign(
        workspace_id=workspace.id, name="Bootstrap Outcome Campaign", prompt="Quiero vender más cursos online.",
        product_type="Curso", price="$99", audience="Emprendedores", budget="$300", channel="Instagram",
    )
    db_session.commit()

    service = OrchestrationService(db_session)
    service.initialize_run(campaign=campaign, run=run, actor_user_id=user.id, request_id=None)
    db_session.commit()
    run = service.start_run(campaign=campaign, run=run, actor_user_id=user.id, request_id=None)
    db_session.commit()
    service.run_deterministic_bootstrap(campaign=campaign, run=run, actor_user_id=user.id, request_id=None)
    db_session.commit()

    campaign_outcome_count = db_session.execute(
        select(func.count()).select_from(CommercialOutcome).where(CommercialOutcome.campaign_id == campaign.id)
    ).scalar_one()
    assert campaign_outcome_count == 0


# --- downstream non-effects ----------------------------------------------------


def test_create_and_correction_touch_only_outcome_and_audit_tables(db_session) -> None:
    """MVP-36A §33/MVP-36B §29: create/correction must not mutate or
    create rows in any other domain's tables."""
    tables = [
        ContentPiece, ContentApproval, ContentDistribution, TrackingRequirement,
        MetricEntry, DistributionMetricEvidence, LearningCandidate,
        StrategicDecision, Experiment, Strategy, CommercialObjective, Offer,
    ]
    before = {model: _total_count(db_session, model) for model in tables}

    user = make_user(db_session)
    campaign, outcome = build_commercial_outcome(db_session, actor_user_id=user.id)
    db_session.commit()
    CommercialService(db_session).correct_commercial_outcome(
        campaign=campaign, target_outcome_public_id=outcome.public_id, outcome_type="purchase",
        quantity=None, monetary_value=None, currency=None,
        occurred_at=datetime(2026, 1, 10, tzinfo=timezone.utc), external_reference=None,
        client_request_id=str(uuid.uuid4()), correction_reason="x", actor_user_id=user.id,
    )
    db_session.commit()

    for model in tables:
        assert _total_count(db_session, model) == before[model], f"{model.__name__} row count changed"


def test_create_and_correction_mutate_only_outcome_and_audit_tables_across_all_tables(db_session) -> None:
    """MVP-36B-R1: the broad, metadata-driven version of the check above —
    EVERY table in the schema (including any additive table a future MVP
    introduces) is snapshotted, so an accidental downstream write anywhere
    fails here. The only permitted deltas are ``commercial_outcomes`` (+2:
    the create and its correction) and ``audit_events`` (+2)."""
    from app.persistence.base import metadata
    from tests.commercialtest import build_campaign

    user = make_user(db_session)
    campaign = build_campaign(db_session)
    db_session.commit()  # setup rows are outside the measured window

    def snapshot() -> dict[str, int]:
        return {
            name: db_session.execute(select(func.count()).select_from(table)).scalar_one()
            for name, table in metadata.tables.items()
        }

    before = snapshot()
    service = CommercialService(db_session)
    outcome, created = service.record_commercial_outcome(
        campaign=campaign, content_distribution_id=None, outcome_type="purchase", quantity=1,
        monetary_value=None, currency=None, occurred_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
        external_reference=None, client_request_id=str(uuid.uuid4()), actor_user_id=user.id,
    )
    assert created is True
    _correction, corrected = service.correct_commercial_outcome(
        campaign=campaign, target_outcome_public_id=outcome.public_id, outcome_type="purchase",
        quantity=2, monetary_value=None, currency=None,
        occurred_at=datetime(2026, 1, 10, tzinfo=timezone.utc), external_reference=None,
        client_request_id=str(uuid.uuid4()), correction_reason="x", actor_user_id=user.id,
    )
    assert corrected is True
    db_session.commit()

    deltas = {name: count - before[name] for name, count in snapshot().items() if count != before[name]}
    assert deltas == {"commercial_outcomes": 2, "audit_events": 2}
