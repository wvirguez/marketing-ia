"""Governance Freeze-R: Requirement mutation lifecycle / certification
integrity matrix (BACKEND-15, TRK-D36..TRK-D41). All marked `postgres`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.api_errors import TrackingRequirementMutationForbiddenError
from app.tracking.models import TrackingReadinessStatus, TrackingRequirement
from app.tracking.service import TrackingService
from tests.contenttest import make_user
from tests.trackingtest import advance_plan_to, build_tracking_plan

pytestmark = pytest.mark.postgres

_ALL_STATES = list(TrackingReadinessStatus)

_CREATION_ALLOWED = {
    TrackingReadinessStatus.NOT_DEFINED,
    TrackingReadinessStatus.REQUIREMENTS_DEFINED,
    TrackingReadinessStatus.CONFIGURATION_PENDING,
    TrackingReadinessStatus.FAILED_VERIFICATION,
}
_CREATION_FORBIDDEN = {
    TrackingReadinessStatus.CONFIGURED,
    TrackingReadinessStatus.VERIFICATION_PENDING,
    TrackingReadinessStatus.CERTIFIED,
}
_STATUS_MUTATION_FORBIDDEN = {TrackingReadinessStatus.CERTIFIED}
_STATUS_MUTATION_ALLOWED = set(_ALL_STATES) - _STATUS_MUTATION_FORBIDDEN

assert _CREATION_ALLOWED | _CREATION_FORBIDDEN == set(_ALL_STATES)
assert _STATUS_MUTATION_ALLOWED | _STATUS_MUTATION_FORBIDDEN == set(_ALL_STATES)


def _audit_event_count(session) -> int:
    from app.audit.models import AuditEvent

    return session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()


@pytest.mark.parametrize("state", sorted(_CREATION_ALLOWED, key=lambda s: s.value))
def test_record_tracking_requirement_allowed_states_succeed(db_session, state) -> None:
    campaign, plan = build_tracking_plan(db_session)
    plan = advance_plan_to(db_session, campaign, plan, state)
    requirement = TrackingService(db_session).record_tracking_requirement(tracking_plan=plan, name="Analytics")
    assert requirement.tracking_plan_id == plan.id


@pytest.mark.parametrize("state", sorted(_CREATION_FORBIDDEN, key=lambda s: s.value))
def test_record_tracking_requirement_forbidden_states_reject_with_no_mutation_or_audit(db_session, state) -> None:
    campaign, plan = build_tracking_plan(db_session)
    plan = advance_plan_to(db_session, campaign, plan, state)
    requirements_before = db_session.execute(select(func.count()).select_from(TrackingRequirement)).scalar_one()
    events_before = _audit_event_count(db_session)

    with pytest.raises(TrackingRequirementMutationForbiddenError):
        TrackingService(db_session).record_tracking_requirement(tracking_plan=plan, name="Analytics")

    assert db_session.execute(select(func.count()).select_from(TrackingRequirement)).scalar_one() == requirements_before
    assert _audit_event_count(db_session) == events_before


@pytest.mark.parametrize("state", sorted(_STATUS_MUTATION_ALLOWED, key=lambda s: s.value))
def test_update_requirement_status_allowed_states_succeed(db_session, state) -> None:
    campaign, plan = build_tracking_plan(db_session)
    requirement = TrackingService(db_session).record_tracking_requirement(tracking_plan=plan, name="Purchase event")
    plan = advance_plan_to(db_session, campaign, plan, state)
    reviewer = make_user(db_session)

    updated = TrackingService(db_session).update_tracking_requirement_status(
        campaign=campaign, requirement_public_id=requirement.public_id, status="Configurado", actor_user_id=reviewer.id
    )
    assert updated.status == "Configurado"


@pytest.mark.parametrize("state", sorted(_STATUS_MUTATION_FORBIDDEN, key=lambda s: s.value))
def test_update_requirement_status_forbidden_states_reject_with_no_mutation_or_audit(db_session, state) -> None:
    campaign, plan = build_tracking_plan(db_session)
    requirement = TrackingService(db_session).record_tracking_requirement(tracking_plan=plan, name="Purchase event")
    plan = advance_plan_to(db_session, campaign, plan, state)
    reviewer = make_user(db_session)
    events_before = _audit_event_count(db_session)

    with pytest.raises(TrackingRequirementMutationForbiddenError):
        TrackingService(db_session).update_tracking_requirement_status(
            campaign=campaign, requirement_public_id=requirement.public_id, status="Configurado", actor_user_id=reviewer.id
        )

    db_session.refresh(requirement)
    assert requirement.status is None
    assert _audit_event_count(db_session) == events_before


def test_null_clearing_is_a_legitimate_mutation(db_session) -> None:
    campaign, plan = build_tracking_plan(db_session)
    requirement = TrackingService(db_session).record_tracking_requirement(tracking_plan=plan, name="Purchase event")
    reviewer = make_user(db_session)
    service = TrackingService(db_session)

    updated = service.update_tracking_requirement_status(
        campaign=campaign, requirement_public_id=requirement.public_id, status="Pendiente", actor_user_id=reviewer.id
    )
    assert updated.status == "Pendiente"

    cleared = service.update_tracking_requirement_status(
        campaign=campaign, requirement_public_id=requirement.public_id, status=None, actor_user_id=reviewer.id
    )
    assert cleared.status is None


def test_certified_integrity_requirement_set_and_statuses_are_frozen(db_session) -> None:
    campaign, plan = build_tracking_plan(db_session)
    requirement = TrackingService(db_session).record_tracking_requirement(tracking_plan=plan, name="Purchase event")
    plan = advance_plan_to(db_session, campaign, plan, TrackingReadinessStatus.CERTIFIED)
    reviewer = make_user(db_session)
    service = TrackingService(db_session)

    requirements_before = db_session.execute(select(func.count()).select_from(TrackingRequirement)).scalar_one()

    with pytest.raises(TrackingRequirementMutationForbiddenError):
        service.record_tracking_requirement(tracking_plan=plan, name="A new one")
    with pytest.raises(TrackingRequirementMutationForbiddenError):
        service.update_tracking_requirement_status(
            campaign=campaign, requirement_public_id=requirement.public_id, status="Something", actor_user_id=reviewer.id
        )

    assert db_session.execute(select(func.count()).select_from(TrackingRequirement)).scalar_one() == requirements_before
    db_session.refresh(requirement)
    assert requirement.status is None
    db_session.refresh(plan)
    assert plan.status is TrackingReadinessStatus.CERTIFIED


def test_requirement_mutation_never_automatically_changes_plan_status(db_session) -> None:
    campaign, plan = build_tracking_plan(db_session)
    reviewer = make_user(db_session)
    service = TrackingService(db_session)
    requirement = service.record_tracking_requirement(tracking_plan=plan, name="Purchase event")
    db_session.refresh(plan)
    assert plan.status is TrackingReadinessStatus.NOT_DEFINED

    service.update_tracking_requirement_status(
        campaign=campaign, requirement_public_id=requirement.public_id, status="Configurado", actor_user_id=reviewer.id
    )
    db_session.refresh(plan)
    assert plan.status is TrackingReadinessStatus.NOT_DEFINED


def test_internal_creation_is_not_exempt_from_lifecycle_invariant(db_session) -> None:
    """record_tracking_requirement is service-only, but its internal
    status does NOT exempt it from TRK-D36 (TRK-D41) — a caller with no
    public route still cannot bypass the domain invariant."""
    campaign, plan = build_tracking_plan(db_session)
    plan = advance_plan_to(db_session, campaign, plan, TrackingReadinessStatus.CERTIFIED)
    with pytest.raises(TrackingRequirementMutationForbiddenError):
        TrackingService(db_session).record_tracking_requirement(tracking_plan=plan, name="Bypass attempt")
