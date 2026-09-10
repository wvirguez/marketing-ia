"""Tracking domain persistence, tenancy, cardinality, and lifecycle tests
(BACKEND-15). All marked `postgres`.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.api_errors import InvalidLifecycleTransitionError, TrackingPlanAlreadyExistsError
from app.tracking.models import TrackingPlan, TrackingReadinessStatus, TrackingRequirement
from app.tracking.service import TrackingService
from app.tracking.transitions import (
    TRACKING_PLAN_TRANSITIONS,
    is_legal_tracking_plan_transition,
)
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository
from tests.contenttest import make_user
from tests.trackingtest import advance_plan_to, build_campaign, build_tracking_plan, build_tracking_requirement

pytestmark = pytest.mark.postgres


# --- TrackingPlan: domain persistence --------------------------------


def test_tracking_plan_persists_with_correct_workspace_campaign_and_initial_status(db_session) -> None:
    campaign, plan = build_tracking_plan(db_session)
    assert plan.public_id.startswith("TRK-")
    assert plan.workspace_id == campaign.workspace_id
    assert plan.campaign_id == campaign.id
    assert plan.status is TrackingReadinessStatus.NOT_DEFINED


def test_tracking_readiness_status_enum_membership_is_exact() -> None:
    assert {s.value for s in TrackingReadinessStatus} == {
        "NOT_DEFINED", "REQUIREMENTS_DEFINED", "CONFIGURATION_PENDING", "CONFIGURED",
        "VERIFICATION_PENDING", "FAILED_VERIFICATION", "CERTIFIED",
    }


def test_second_plan_for_same_campaign_is_rejected(db_session) -> None:
    campaign, _plan = build_tracking_plan(db_session)
    with pytest.raises(TrackingPlanAlreadyExistsError):
        TrackingService(db_session).record_tracking_plan(campaign=campaign)


def test_tracking_plan_workspace_mismatch_with_campaign_rejected_at_db_level(db_session) -> None:
    campaign = build_campaign(db_session)
    other_workspace = WorkspaceRepository(db_session).create(
        organization_id=OrganizationRepository(db_session).create(name="Rogue Org").id, name="Rogue WS"
    )
    db_session.flush()

    rogue = TrackingPlan(public_id="TRK-MISMATCHTEST", workspace_id=other_workspace.id, campaign_id=campaign.id)
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_no_forbidden_fields_exist_on_tracking_plan() -> None:
    forbidden = (
        "updated_at", "version", "version_number", "archived_at", "deleted_at",
        "configuration", "provider", "measurement", "learning",
    )
    columns = [c.lower() for c in TrackingPlan.__table__.columns.keys()]
    for term in forbidden:
        assert term not in columns, f"unexpected field {term!r} on TrackingPlan"


def test_tracking_plan_has_no_speculative_candidate_key() -> None:
    constraint_names = {c.name for c in TrackingPlan.__table__.constraints if getattr(c, "name", None)}
    assert "uq_tracking_plans_id_workspace_id" not in constraint_names
    assert "uq_tracking_plans_campaign_workspace" in constraint_names


# --- TrackingRequirement: domain persistence --------------------------


def test_tracking_requirement_persists_under_plan_with_null_status(db_session) -> None:
    _campaign, plan, requirement = build_tracking_requirement(db_session)
    assert requirement.public_id.startswith("TRQ-")
    assert requirement.tracking_plan_id == plan.id
    assert requirement.status is None


def test_plan_can_own_multiple_requirements(db_session) -> None:
    _campaign, plan = build_tracking_plan(db_session)
    service = TrackingService(db_session)
    a = service.record_tracking_requirement(tracking_plan=plan, name="Landing page")
    b = service.record_tracking_requirement(tracking_plan=plan, name="Checkout")
    assert a.id != b.id
    assert a.tracking_plan_id == b.tracking_plan_id == plan.id


def test_no_forbidden_fields_exist_on_tracking_requirement() -> None:
    forbidden = (
        "workspace_id", "campaign_id", "type", "kind", "description", "configuration",
        "verification_metadata", "provider_id", "pixel_id", "event_type", "channel_id",
        "content_piece_id", "metric_entry_id", "analysis_result_id", "updated_at",
        "archived_at", "deleted_at", "version",
    )
    columns = [c.lower() for c in TrackingRequirement.__table__.columns.keys()]
    for term in forbidden:
        assert term not in columns, f"unexpected field {term!r} on TrackingRequirement"


def test_tracking_requirement_name_and_status_lengths() -> None:
    assert TrackingRequirement.__table__.columns["name"].type.length == 255
    assert TrackingRequirement.__table__.columns["status"].type.length == 30
    assert TrackingRequirement.__table__.columns["status"].nullable is True


# --- state machine -----------------------------------------------------


def test_every_allowed_transition_succeeds(db_session) -> None:
    campaign, plan = build_tracking_plan(db_session)
    reviewer = make_user(db_session)
    service = TrackingService(db_session)
    service.record_tracking_requirement(tracking_plan=plan, name="Purchase event")

    plan = service.transition_tracking_plan(campaign=campaign, target_status=TrackingReadinessStatus.REQUIREMENTS_DEFINED, actor_user_id=reviewer.id)
    assert plan.status is TrackingReadinessStatus.REQUIREMENTS_DEFINED
    plan = service.transition_tracking_plan(campaign=campaign, target_status=TrackingReadinessStatus.CONFIGURATION_PENDING, actor_user_id=reviewer.id)
    assert plan.status is TrackingReadinessStatus.CONFIGURATION_PENDING
    plan = service.transition_tracking_plan(campaign=campaign, target_status=TrackingReadinessStatus.FAILED_VERIFICATION, actor_user_id=reviewer.id)
    assert plan.status is TrackingReadinessStatus.FAILED_VERIFICATION
    plan = service.transition_tracking_plan(campaign=campaign, target_status=TrackingReadinessStatus.CONFIGURATION_PENDING, actor_user_id=reviewer.id)
    assert plan.status is TrackingReadinessStatus.CONFIGURATION_PENDING
    plan = service.transition_tracking_plan(campaign=campaign, target_status=TrackingReadinessStatus.CONFIGURED, actor_user_id=reviewer.id)
    assert plan.status is TrackingReadinessStatus.CONFIGURED
    plan = service.transition_tracking_plan(campaign=campaign, target_status=TrackingReadinessStatus.VERIFICATION_PENDING, actor_user_id=reviewer.id)
    assert plan.status is TrackingReadinessStatus.VERIFICATION_PENDING
    plan = service.transition_tracking_plan(campaign=campaign, target_status=TrackingReadinessStatus.CERTIFIED, actor_user_id=reviewer.id)
    assert plan.status is TrackingReadinessStatus.CERTIFIED


def test_verification_pending_can_reach_failed_verification(db_session) -> None:
    campaign, plan = build_tracking_plan(db_session)
    plan = advance_plan_to(db_session, campaign, plan, TrackingReadinessStatus.VERIFICATION_PENDING)
    reviewer = make_user(db_session)
    plan = TrackingService(db_session).transition_tracking_plan(
        campaign=campaign, target_status=TrackingReadinessStatus.FAILED_VERIFICATION, actor_user_id=reviewer.id
    )
    assert plan.status is TrackingReadinessStatus.FAILED_VERIFICATION


@pytest.mark.parametrize(
    "current,target",
    [
        (TrackingReadinessStatus.NOT_DEFINED, TrackingReadinessStatus.CONFIGURATION_PENDING),
        (TrackingReadinessStatus.NOT_DEFINED, TrackingReadinessStatus.CERTIFIED),
        (TrackingReadinessStatus.REQUIREMENTS_DEFINED, TrackingReadinessStatus.CONFIGURED),
        (TrackingReadinessStatus.REQUIREMENTS_DEFINED, TrackingReadinessStatus.FAILED_VERIFICATION),
        (TrackingReadinessStatus.CONFIGURED, TrackingReadinessStatus.REQUIREMENTS_DEFINED),
        (TrackingReadinessStatus.CERTIFIED, TrackingReadinessStatus.VERIFICATION_PENDING),
        (TrackingReadinessStatus.FAILED_VERIFICATION, TrackingReadinessStatus.CERTIFIED),
        (TrackingReadinessStatus.FAILED_VERIFICATION, TrackingReadinessStatus.VERIFICATION_PENDING),
    ],
)
def test_every_forbidden_transition_is_rejected(current, target) -> None:
    assert not is_legal_tracking_plan_transition(current, target)


def test_certified_is_terminal() -> None:
    assert TRACKING_PLAN_TRANSITIONS[TrackingReadinessStatus.CERTIFIED] == frozenset()


def test_failed_verification_dual_entry_single_exit() -> None:
    assert TrackingReadinessStatus.FAILED_VERIFICATION in TRACKING_PLAN_TRANSITIONS[TrackingReadinessStatus.CONFIGURATION_PENDING]
    assert TrackingReadinessStatus.FAILED_VERIFICATION in TRACKING_PLAN_TRANSITIONS[TrackingReadinessStatus.VERIFICATION_PENDING]
    assert TRACKING_PLAN_TRANSITIONS[TrackingReadinessStatus.FAILED_VERIFICATION] == frozenset(
        {TrackingReadinessStatus.CONFIGURATION_PENDING}
    )


def test_invalid_transition_raises_and_causes_no_mutation(db_session) -> None:
    campaign, plan = build_tracking_plan(db_session)
    reviewer = make_user(db_session)
    with pytest.raises(InvalidLifecycleTransitionError):
        TrackingService(db_session).transition_tracking_plan(
            campaign=campaign, target_status=TrackingReadinessStatus.CERTIFIED, actor_user_id=reviewer.id
        )
    db_session.refresh(plan)
    assert plan.status is TrackingReadinessStatus.NOT_DEFINED


# --- transition precondition: existence-only, never status-value based --


def test_requirements_defined_transition_fails_with_zero_requirements(db_session) -> None:
    campaign, _plan = build_tracking_plan(db_session)
    reviewer = make_user(db_session)
    with pytest.raises(InvalidLifecycleTransitionError):
        TrackingService(db_session).transition_tracking_plan(
            campaign=campaign, target_status=TrackingReadinessStatus.REQUIREMENTS_DEFINED, actor_user_id=reviewer.id
        )


def test_requirements_defined_transition_succeeds_with_one_requirement_regardless_of_its_status(db_session) -> None:
    campaign, plan = build_tracking_plan(db_session)
    requirement = TrackingService(db_session).record_tracking_requirement(tracking_plan=plan, name="Purchase event")
    assert requirement.status is None  # precondition never inspects requirement.status
    reviewer = make_user(db_session)
    plan = TrackingService(db_session).transition_tracking_plan(
        campaign=campaign, target_status=TrackingReadinessStatus.REQUIREMENTS_DEFINED, actor_user_id=reviewer.id
    )
    assert plan.status is TrackingReadinessStatus.REQUIREMENTS_DEFINED


def test_governance_no_forbidden_tables_or_methods() -> None:
    from app.persistence.base import metadata

    for forbidden_table in ("tracking_status", "tracking_readiness", "channels", "tracking_configurations"):
        assert forbidden_table not in metadata.tables.keys()

    forbidden_methods = ("update", "delete", "patch", "recompute_tracking_status", "archive_tracking_plan")
    for method in forbidden_methods:
        assert not hasattr(TrackingService, method), f"unexpected method {method!r} on TrackingService"
