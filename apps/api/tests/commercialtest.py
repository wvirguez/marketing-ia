"""Shared helpers/fixtures for Commercial tests — real database required.
No public write endpoint bypass exists for CommercialObjective/Offer, so
most domain-level tests exercise ``CommercialService`` directly against
real domain objects, the same "construct a controlled fixture" pattern
already used throughout ``tests/trackingtest.py``/``tests/learningtest.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.campaigns.repository import CampaignRepository
from app.campaigns.service import CampaignService
from app.commercial.service import CommercialService
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository
from tests.contenttest import make_user


def build_campaign(session, *, org_name="Commercial Org", workspace_name="Commercial WS", campaign_name="Commercial Campaign"):
    """Creates Organization/Workspace/Campaign only — CommercialObjective/
    Offer's sole upstream dependency (Campaign 1 -> 0..N of each). No
    CampaignBrief — matches ``tests/trackingtest.py::build_campaign``
    exactly for domains that never read/write a Brief."""
    organization = OrganizationRepository(session).create(name=org_name)
    workspace = WorkspaceRepository(session).create(organization_id=organization.id, name=workspace_name)
    campaign = CampaignRepository(session).create(workspace_id=workspace.id, name=campaign_name)
    session.flush()
    return campaign


def build_campaign_with_brief(
    session, *, org_name="Commercial Org", workspace_name="Commercial WS", campaign_name="Commercial Campaign"
):
    """Creates a full Campaign + CampaignBrief (via the real
    ``CampaignService.create_campaign`` atomic path) — used only by the
    CampaignBrief-boundary tests, which must prove a real, populated
    Brief is left untouched by Commercial writes (MVP-27A-R1 §I)."""
    organization = OrganizationRepository(session).create(name=org_name)
    workspace = WorkspaceRepository(session).create(organization_id=organization.id, name=workspace_name)
    campaign, brief, _run = CampaignService(session).create_campaign(
        workspace_id=workspace.id,
        name=campaign_name,
        prompt="Quiero lanzar un curso sobre adiestramiento canino en casa.",
        product_type="Curso online",
        price="$199 USD",
        audience="Dueños primerizos de perros",
        budget="$500 en ads",
        channel="Instagram",
    )
    return campaign, brief


def build_commercial_objective(session, *, statement="Generate qualified leads for the new course.", **overrides: object):
    campaign = build_campaign(session, **overrides)
    objective = CommercialService(session).record_commercial_objective(campaign=campaign, statement=statement)
    return campaign, objective


def build_offer(session, *, statement="Six-week home dog training course.", price=None, currency=None, **overrides: object):
    campaign = build_campaign(session, **overrides)
    offer = CommercialService(session).record_offer(campaign=campaign, statement=statement, price=price, currency=currency)
    return campaign, offer


def build_commercial_outcome(
    session,
    *,
    outcome_type="lead",
    quantity=None,
    monetary_value=None,
    currency=None,
    occurred_at=None,
    content_distribution_id=None,
    external_reference=None,
    client_request_id=None,
    actor_user_id=None,
    **overrides: object,
):
    """MVP-36: the sole upstream dependency for CommercialOutcome tests
    that don't need a real ContentDistribution — mirrors
    ``build_commercial_objective``/``build_offer`` exactly."""
    campaign = build_campaign(session, **overrides)
    if actor_user_id is None:
        actor_user_id = make_user(session).id
    outcome, _created = CommercialService(session).record_commercial_outcome(
        campaign=campaign,
        content_distribution_id=content_distribution_id,
        outcome_type=outcome_type,
        quantity=quantity,
        monetary_value=monetary_value,
        currency=currency,
        occurred_at=occurred_at or datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
        external_reference=external_reference,
        client_request_id=client_request_id or str(uuid.uuid4()),
        actor_user_id=actor_user_id,
    )
    return campaign, outcome
