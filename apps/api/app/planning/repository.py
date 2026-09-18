"""Data access for Content Plan / Plan Item. No repository here calls
``session.commit()`` — see ``app/persistence/session.py`` and
``app/planning/service.py`` for the transaction-ownership boundary.

Every ``create``/``create_many`` method takes the parent domain object
(``Campaign``, ``CampaignRun``, ``RunStageExecution``, ``ContentPlan``),
never a raw ``workspace_id``/``campaign_id``/``content_plan_id`` parameter —
the same "no independent parameter, no possibility of drift" pattern
established in ``app/strategy/repository.py``/``app/research/repository.py``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.campaigns.models import Campaign, CampaignRun
from app.core.ids import generate_public_id
from app.orchestration.models import RunStageExecution
from app.planning.models import ContentPlan, ContentPlanOrigin, PlanItem
from app.strategy.models import Experiment


class ContentPlanRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        campaign: Campaign,
        version: int,
        summary: str,
        origin: ContentPlanOrigin,
        campaign_run: CampaignRun | None = None,
        stage_execution: RunStageExecution | None = None,
        experiment: Experiment | None = None,
    ) -> ContentPlan:
        """``campaign_run``/``stage_execution`` are required together for
        ``origin=BOOTSTRAP`` and must be omitted together for
        ``origin=GOVERNED`` (MVP-33A/-33A-R1) — the DB's own
        ``ck_content_plans_origin_bootstrap_fields`` CHECK constraint is
        the actual backstop, never trusted as merely an application-level
        convention (mirrors ``StrategyRepository.create``'s own
        origin/campaign_run/stage_execution precedent exactly). ``version``
        is always server-derived by the caller — never accepted from a
        client. ``experiment`` is optional for either origin, but the DB's
        own ``ck_content_plans_bootstrap_experiment_null`` CHECK constraint
        additionally guarantees a BOOTSTRAP row can never carry one."""
        plan = ContentPlan(
            public_id=generate_public_id("PLN"),
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.id,
            origin=origin,
            campaign_run_id=campaign_run.id if campaign_run is not None else None,
            stage_execution_id=stage_execution.id if stage_execution is not None else None,
            experiment_id=experiment.id if experiment is not None else None,
            version=version,
            summary=summary,
        )
        self.session.add(plan)
        self.session.flush()
        return plan

    def get_by_public_id(self, public_id: str) -> ContentPlan | None:
        return self.session.execute(select(ContentPlan).where(ContentPlan.public_id == public_id)).scalar_one_or_none()

    def get_by_id(self, content_plan_id: uuid.UUID) -> ContentPlan | None:
        """MVP-34B: readback-only, mirrors ``ExperimentRepository.get_by_id``
        — used to resolve the exact parent ContentPlan of an already
        campaign-scoped PlanItem (``PlanItemRepository.get_for_campaign_by_
        public_id``), never independently re-derived from "current"."""
        return self.session.get(ContentPlan, content_plan_id)

    def get_current_for_campaign(self, campaign_id: uuid.UUID) -> ContentPlan | None:
        return self.session.execute(
            select(ContentPlan).where(ContentPlan.campaign_id == campaign_id).order_by(ContentPlan.version.desc()).limit(1)
        ).scalar_one_or_none()

    def next_version_for_campaign(self, campaign_id: uuid.UUID) -> int:
        current = self.session.execute(
            select(func.max(ContentPlan.version)).where(ContentPlan.campaign_id == campaign_id)
        ).scalar_one()
        return (current or 0) + 1


class PlanItemRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_many(self, *, plan: ContentPlan, items: list[dict]) -> list[PlanItem]:
        rows = [
            PlanItem(
                public_id=generate_public_id("ITM"),
                content_plan_id=plan.id,
                format=item["format"],
                objective=item["objective"],
                sequence=item["sequence"],
                scheduled_date=item.get("scheduled_date"),
            )
            for item in items
        ]
        self.session.add_all(rows)
        self.session.flush()
        return rows

    def list_for_plan(self, content_plan_id: uuid.UUID) -> list[PlanItem]:
        return list(
            self.session.execute(
                select(PlanItem).where(PlanItem.content_plan_id == content_plan_id).order_by(PlanItem.sequence.asc(), PlanItem.id.asc())
            )
            .scalars()
            .all()
        )

    def get_for_campaign_by_public_id(self, *, campaign_id: uuid.UUID, public_id: str) -> PlanItem | None:
        """MVP-34A §F/MVP-34B: non-leaky, campaign-scoped resolution — the
        same join-based technique already used by
        ``ContentPieceRepository.get_for_campaign_by_public_id``/
        ``ExperimentRepository.get_for_campaign_by_public_id``, one join
        level shallower. Deliberately unfiltered by ContentPlan currency —
        a PlanItem belonging to any version (current or historical) of this
        Campaign's Content Plan is eligible (MVP-34A §G: historical
        eligibility is a frozen contract decision, not an oversight). A
        PlanItem that does not exist, or that exists but belongs to a
        different Campaign, is indistinguishable — both return ``None``."""
        return self.session.execute(
            select(PlanItem)
            .join(ContentPlan, PlanItem.content_plan_id == ContentPlan.id)
            .where(ContentPlan.campaign_id == campaign_id, PlanItem.public_id == public_id)
        ).scalar_one_or_none()
