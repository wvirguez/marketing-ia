"""Data access for RunStageExecution / HumanDecisionRequest /
HumanDecisionResponse. No repository here calls ``session.commit()`` —
see ``app/persistence/session.py`` and ``app/orchestration/service.py``
for the transaction-ownership boundary.

Every ``create`` method here takes the parent domain object (a
``CampaignRun``, never a raw ``workspace_id``) and derives tenant
ownership from it — the same "no independent parameter, no possibility
of drift" pattern used for ``CampaignRun`` itself
(``app/campaigns/repository.py``), applied consistently one level down
(BACKEND-06 §6).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.campaigns.models import CampaignRun
from app.core.ids import generate_public_id
from app.orchestration.models import (
    BUSINESS_STAGE_ORDER,
    DecisionRequestStatus,
    HumanDecisionRequest,
    HumanDecisionResponse,
    RunStageExecution,
    StageExecutionStatus,
)


class RunStageExecutionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def materialize_for_run(self, *, campaign_run: CampaignRun) -> list[RunStageExecution]:
        stages = [
            RunStageExecution(
                public_id=generate_public_id("STG"),
                workspace_id=campaign_run.workspace_id,
                campaign_run_id=campaign_run.id,
                stage=stage,
                ordinal=ordinal,
                status=StageExecutionStatus.PENDING,
            )
            for ordinal, stage in enumerate(BUSINESS_STAGE_ORDER, start=1)
        ]
        self.session.add_all(stages)
        self.session.flush()
        return stages

    def get_by_id(self, stage_execution_id: uuid.UUID) -> RunStageExecution | None:
        return self.session.get(RunStageExecution, stage_execution_id)

    def list_for_run(self, *, campaign_run_id: uuid.UUID) -> list[RunStageExecution]:
        return list(
            self.session.execute(
                select(RunStageExecution)
                .where(RunStageExecution.campaign_run_id == campaign_run_id)
                .order_by(RunStageExecution.ordinal.asc())
            )
            .scalars()
            .all()
        )

    def get_by_ordinal_for_update(self, *, campaign_run_id: uuid.UUID, ordinal: int) -> RunStageExecution | None:
        return self.session.execute(
            select(RunStageExecution)
            .where(RunStageExecution.campaign_run_id == campaign_run_id, RunStageExecution.ordinal == ordinal)
            .with_for_update()
        ).scalar_one_or_none()


class HumanDecisionRequestRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self, *, campaign_run: CampaignRun, question: str, stage_execution_id: uuid.UUID | None = None
    ) -> HumanDecisionRequest:
        request = HumanDecisionRequest(
            public_id=generate_public_id("HDR"),
            workspace_id=campaign_run.workspace_id,
            campaign_run_id=campaign_run.id,
            stage_execution_id=stage_execution_id,
            question=question,
            status=DecisionRequestStatus.OPEN,
        )
        self.session.add(request)
        self.session.flush()
        return request

    def get_by_public_id(self, public_id: str, *, for_update: bool = False) -> HumanDecisionRequest | None:
        query = select(HumanDecisionRequest).where(HumanDecisionRequest.public_id == public_id)
        if for_update:
            query = query.with_for_update()
        return self.session.execute(query).scalar_one_or_none()

    def get_by_id(self, decision_request_id: uuid.UUID) -> HumanDecisionRequest | None:
        return self.session.get(HumanDecisionRequest, decision_request_id)

    def list_for_run(self, *, campaign_run_id: uuid.UUID, limit: int, offset: int) -> tuple[list, int]:
        conditions = [HumanDecisionRequest.campaign_run_id == campaign_run_id]
        total = self.session.execute(
            select(func.count()).select_from(HumanDecisionRequest).where(*conditions)
        ).scalar_one()
        items = (
            self.session.execute(
                select(HumanDecisionRequest)
                .where(*conditions)
                .order_by(HumanDecisionRequest.created_at.asc(), HumanDecisionRequest.id.asc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
        return list(items), total


class HumanDecisionResponseRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self, *, decision_request: HumanDecisionRequest, responded_by_user_id: uuid.UUID, response_text: str
    ) -> HumanDecisionResponse:
        response = HumanDecisionResponse(
            public_id=generate_public_id("HDS"),
            workspace_id=decision_request.workspace_id,
            decision_request_id=decision_request.id,
            responded_by_user_id=responded_by_user_id,
            response_text=response_text,
        )
        self.session.add(response)
        self.session.flush()
        return response

    def get_for_request(self, decision_request_id: uuid.UUID) -> HumanDecisionResponse | None:
        return self.session.execute(
            select(HumanDecisionResponse).where(HumanDecisionResponse.decision_request_id == decision_request_id)
        ).scalar_one_or_none()
