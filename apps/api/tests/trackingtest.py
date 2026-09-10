"""Shared helpers/fixtures for Tracking tests — real database required.
No public write endpoint exists for TrackingPlan/TrackingRequirement
creation in BACKEND-15, so most tests exercise ``TrackingService``
directly against real domain objects, the same "construct a controlled
fixture" pattern already used throughout ``tests/learningtest.py``/
``tests/assetstest.py``.
"""

from __future__ import annotations

from app.campaigns.repository import CampaignRepository
from app.tracking.models import TrackingReadinessStatus
from app.tracking.service import TrackingService
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository
from tests.contenttest import make_user


def build_campaign(session, *, org_name="Tracking Org", workspace_name="Tracking WS", campaign_name="Tracking Campaign"):
    """Creates Organization/Workspace/Campaign only — TrackingPlan's sole
    upstream dependency (Campaign 1 -> 0..1 TrackingPlan, TRK-D01)."""
    organization = OrganizationRepository(session).create(name=org_name)
    workspace = WorkspaceRepository(session).create(organization_id=organization.id, name=workspace_name)
    campaign = CampaignRepository(session).create(workspace_id=workspace.id, name=campaign_name)
    session.flush()
    return campaign


def build_tracking_plan(session, **overrides: object):
    campaign = build_campaign(session, **overrides)
    plan = TrackingService(session).record_tracking_plan(campaign=campaign)
    return campaign, plan


def build_tracking_requirement(session, *, name="Purchase event", **overrides: object):
    campaign, plan = build_tracking_plan(session, **overrides)
    requirement = TrackingService(session).record_tracking_requirement(tracking_plan=plan, name=name)
    return campaign, plan, requirement


def advance_plan_to(session, campaign, plan, target: TrackingReadinessStatus, *, via_failed_verification=False):
    """Drives a freshly-recorded (NOT_DEFINED) TrackingPlan forward along
    the canonical path to ``target``, adding exactly one Requirement to
    satisfy the NOT_DEFINED -> REQUIREMENTS_DEFINED existence precondition
    (TRK-D28). Uses a real User for actor_user_id — both PATCH-facing
    commands require it, and AuditEvent.actor_user_id carries a real FK
    to users.id (a fabricated UUID here would violate it)."""
    service = TrackingService(session)
    reviewer = make_user(session)
    order = [
        TrackingReadinessStatus.NOT_DEFINED,
        TrackingReadinessStatus.REQUIREMENTS_DEFINED,
        TrackingReadinessStatus.CONFIGURATION_PENDING,
        TrackingReadinessStatus.CONFIGURED,
        TrackingReadinessStatus.VERIFICATION_PENDING,
        TrackingReadinessStatus.CERTIFIED,
    ]
    if plan.status is TrackingReadinessStatus.NOT_DEFINED and target is not TrackingReadinessStatus.NOT_DEFINED:
        service.record_tracking_requirement(tracking_plan=plan, name="Purchase event")

    if target is TrackingReadinessStatus.FAILED_VERIFICATION:
        pivot = TrackingReadinessStatus.VERIFICATION_PENDING if via_failed_verification else TrackingReadinessStatus.CONFIGURATION_PENDING
        advance_plan_to(session, campaign, plan, pivot)
        return service.transition_tracking_plan(
            campaign=campaign, target_status=TrackingReadinessStatus.FAILED_VERIFICATION, actor_user_id=reviewer.id
        )

    target_index = order.index(target)
    current_index = order.index(plan.status)
    for status in order[current_index + 1 : target_index + 1]:
        plan = service.transition_tracking_plan(campaign=campaign, target_status=status, actor_user_id=reviewer.id)
    return plan
