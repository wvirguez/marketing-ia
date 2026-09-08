"""BACKEND-06 §6: CampaignRun.workspace_id MUST equal Campaign.workspace_id
— proven at both the application layer (structurally impossible to
diverge, since the repository derives it from the Campaign object and
never accepts it as an independent parameter) and the database layer
(a composite foreign key rejects a mismatched row outright, even when
constructed directly, bypassing every application-level guard).
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.campaigns.models import CampaignRun
from app.campaigns.repository import CampaignRepository, CampaignRunRepository
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository

pytestmark = pytest.mark.postgres


def test_campaign_run_workspace_is_always_derived_from_its_campaign(db_session) -> None:
    """`CampaignRunRepository.create` takes the `Campaign` object, not a
    `workspace_id` parameter — there is no supported application path
    through which a caller could even attempt to pass a mismatched
    value."""
    organization = OrganizationRepository(db_session).create(name="Invariant Org")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="Invariant WS")
    campaign = CampaignRepository(db_session).create(workspace_id=workspace.id, name="Invariant Campaign")

    run = CampaignRunRepository(db_session).create(campaign=campaign, run_number=1)

    assert run.workspace_id == campaign.workspace_id


def test_database_rejects_a_campaign_run_with_a_mismatched_workspace(db_session) -> None:
    """Bypasses the repository/service entirely — constructs a
    `CampaignRun` row directly with a `workspace_id` that does not match
    its `campaign_id`'s real workspace, proving the database's own
    composite foreign key (``fk_campaign_runs_campaign_workspace``)
    rejects it independent of any application-level guard."""
    organization_a = OrganizationRepository(db_session).create(name="Org A")
    workspace_a = WorkspaceRepository(db_session).create(organization_id=organization_a.id, name="WS A")
    campaign_a = CampaignRepository(db_session).create(workspace_id=workspace_a.id, name="Campaign A")

    organization_b = OrganizationRepository(db_session).create(name="Org B")
    workspace_b = WorkspaceRepository(db_session).create(organization_id=organization_b.id, name="WS B")
    db_session.flush()

    rogue_run = CampaignRun(
        public_id="RUN-MISMATCHTEST",
        workspace_id=workspace_b.id,  # deliberately wrong: campaign_a belongs to workspace_a
        campaign_id=campaign_a.id,
        run_number=999,
    )
    db_session.add(rogue_run)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_database_accepts_a_campaign_run_with_the_matching_workspace(db_session) -> None:
    """Sanity check for the composite FK's positive case — the exact
    same direct-construction path succeeds when the workspace does
    match, proving the constraint isn't accidentally rejecting
    everything."""
    organization = OrganizationRepository(db_session).create(name="Matching Org")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="Matching WS")
    campaign = CampaignRepository(db_session).create(workspace_id=workspace.id, name="Matching Campaign")
    db_session.flush()

    honest_run = CampaignRun(
        public_id="RUN-MATCHINGTEST",
        workspace_id=workspace.id,
        campaign_id=campaign.id,
        run_number=1,
    )
    db_session.add(honest_run)
    db_session.flush()  # must not raise

    assert honest_run.id is not None
