"""Shared helpers/fixtures for research/audience tests — real database
required. No public write endpoint exists in BACKEND-07 (§15), so most
tests exercise ``ResearchService`` directly against real domain objects,
the same "construct a controlled fixture" pattern already used
throughout ``tests/test_orchestration_*.py``.
"""

from __future__ import annotations

import pytest

from app.campaigns.repository import CampaignRepository, CampaignRunRepository
from app.orchestration.repository import RunStageExecutionRepository
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository


def build_campaign_run_with_stages(session, *, org_name="Research Org", workspace_name="Research WS", campaign_name="Research Campaign"):
    """Creates Organization/Workspace/Campaign/CampaignRun + all 11
    materialized RunStageExecution rows. Returns
    ``(campaign, run, stages_by_business_stage)``."""
    organization = OrganizationRepository(session).create(name=org_name)
    workspace = WorkspaceRepository(session).create(organization_id=organization.id, name=workspace_name)
    campaign = CampaignRepository(session).create(workspace_id=workspace.id, name=campaign_name)
    run = CampaignRunRepository(session).create(campaign=campaign, run_number=1)
    session.flush()
    stages = RunStageExecutionRepository(session).materialize_for_run(campaign_run=run)
    stages_by_name = {stage.stage: stage for stage in stages}
    return campaign, run, stages_by_name


def default_source(**overrides: object) -> dict:
    payload = {
        "source_type": "ARTICLE",
        "title": "Market survey on home dog training",
        "locator": "https://example.com/articles/dog-training-survey",
        "publisher": "Example Research Co.",
        "excerpt": "Many first-time owners report uncertainty about where to start.",
    }
    payload.update(overrides)
    return payload


def default_voc(**overrides: object) -> dict:
    payload = {
        "verbatim_quote": "I just don't know where to even begin with training him.",
        "paraphrase": None,
        "source_type": "SOCIAL",
        "source_locator": "https://example.com/forum/thread-42",
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def research_campaign(db_session):
    campaign, run, stages = build_campaign_run_with_stages(db_session)
    return campaign, run, stages
