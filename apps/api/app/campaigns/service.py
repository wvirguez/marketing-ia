"""Campaign orchestration-free business logic: atomic creation, patch,
archive, and the tenant-isolation gate for by-public-id lookups.

Transaction ownership follows the BACKEND-04 precedent
(``app/auth/service.py``): every public method here calls
``self.session.commit()`` itself, exactly once, after every write for
that operation has succeeded. ``create_campaign`` never calls
``commit()`` until the Campaign, its Campaign Brief v1, and its Campaign
Run #1 have all been created and flushed — if anything raises before
that point, nothing has been committed, and the exception propagates up
to ``get_db``'s own rollback-on-exception handling, so a Campaign can
never be left without its required initial Brief or Run (BACKEND-05 §9).

CampaignRun creation here is intentionally inert: it only ever persists
a row with ``status=CREATED`` (see ``app/campaigns/models.py``). Nothing
in this module invokes an agent, calls an AI provider, or produces any
external side effect. PERSISTED RUN != EXECUTED ORCHESTRATION.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.campaigns.models import Campaign, CampaignBrief, CampaignRun
from app.campaigns.repository import CampaignBriefRepository, CampaignRepository, CampaignRunRepository
from app.core.api_errors import ForbiddenError

_INITIAL_BRIEF_VERSION = 1
_INITIAL_RUN_NUMBER = 1


class CampaignAccessService:
    """Tenant-isolation gate for by-public-id Campaign lookups — the
    Campaign-domain equivalent of
    ``app/workspaces/service.py::WorkspaceAccessService``. Raises the
    exact same ``ForbiddenError`` whether the campaign does not exist at
    all or belongs to a different workspace, so a caller can never
    distinguish "wrong id" from "not yours" (BACKEND-05 §10)."""

    def __init__(self, session: Session) -> None:
        self.campaigns = CampaignRepository(session)

    def get_authorized_campaign(self, *, workspace_id: uuid.UUID, campaign_public_id: str) -> Campaign:
        campaign = self.campaigns.get_by_public_id(campaign_public_id)
        if campaign is None or campaign.workspace_id != workspace_id:
            raise ForbiddenError()
        return campaign


class CampaignService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.campaigns = CampaignRepository(session)
        self.briefs = CampaignBriefRepository(session)
        self.runs = CampaignRunRepository(session)

    def create_campaign(
        self,
        *,
        workspace_id: uuid.UUID,
        name: str,
        prompt: str,
        product_type: str | None,
        price: str | None,
        audience: str | None,
        budget: str | None,
        channel: str | None,
    ) -> tuple[Campaign, CampaignBrief, CampaignRun]:
        campaign = self.campaigns.create(workspace_id=workspace_id, name=name)
        brief = self.briefs.create(
            campaign_id=campaign.id,
            version=_INITIAL_BRIEF_VERSION,
            prompt=prompt,
            product_type=product_type,
            price=price,
            audience=audience,
            budget=budget,
            channel=channel,
        )
        run = self.runs.create(workspace_id=workspace_id, campaign_id=campaign.id, run_number=_INITIAL_RUN_NUMBER)
        self.session.commit()
        return campaign, brief, run

    def patch_campaign(self, *, campaign: Campaign, name: str) -> Campaign:
        campaign.name = name
        self.session.commit()
        return campaign

    def archive_campaign(self, *, campaign: Campaign) -> Campaign:
        """Non-destructive and idempotent: archiving an already-archived
        campaign leaves its original ``archived_at`` untouched rather
        than erroring or bumping the timestamp (BACKEND-05 §17)."""
        if campaign.archived_at is None:
            campaign.archived_at = datetime.now(timezone.utc)
            self.session.commit()
        return campaign
