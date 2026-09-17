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

from app.campaigns.models import Campaign, CampaignRun
from app.core.ids import generate_public_id
from app.orchestration.models import (
    BUSINESS_STAGE_ORDER,
    DecisionRequestStatus,
    HumanDecisionRequest,
    HumanDecisionResponse,
    RunStageExecution,
    StageExecutionStatus,
    StrategicDecision,
    StrategicDecisionType,
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


class StrategicDecisionRepository:
    """Data access for StrategicDecision — MVP-28B (frozen MVP-28A/-R1/-R2
    contract). No method here calls ``session.commit()`` — see
    ``app/orchestration/service.py::StrategicDecisionService`` for the
    transaction-ownership boundary.

    Every ``create`` method takes the parent ``Campaign`` (never a raw
    ``workspace_id``/``campaign_id`` parameter) plus the already
    campaign-scoped-and-locked
    ``StrategicRecommendationCandidate`` (never a raw UUID) — the same
    "no independent parameter, no possibility of drift" pattern this
    codebase uses throughout (see ``app/commercial/repository.py``).
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        campaign: Campaign,
        recommendation_id: uuid.UUID,
        decision_type: StrategicDecisionType,
        statement: str,
        decision_id: uuid.UUID | None = None,
    ) -> StrategicDecision:
        """``decision_id`` lets a caller pre-assign the row's primary key
        (rather than relying on ``UUIDPrimaryKeyMixin``'s own
        ``default=uuid.uuid4``) — needed only by
        ``StrategicDecisionService.supersede_decision``, which must
        reference this row's id from the *original* row's own update
        before this row is inserted (see
        ``app/orchestration/models.py::StrategicDecision``'s "DEFERRABLE"
        docstring note)."""
        row = StrategicDecision(
            id=decision_id if decision_id is not None else uuid.uuid4(),
            public_id=generate_public_id("DEC"),
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.id,
            strategic_recommendation_candidate_id=recommendation_id,
            decision_type=decision_type,
            statement=statement,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_current_for_recommendation(
        self, *, recommendation_id: uuid.UUID, for_update: bool = False
    ) -> StrategicDecision | None:
        """The current-decision invariant's own query rule (MVP-28A-R2 §H):
        ``superseded_at IS NULL`` only, never ``MAX(created_at)``. Locking
        this specific row (never the whole Recommendation) is the canonical
        serialization point for a supersession attempt."""
        query = select(StrategicDecision).where(
            StrategicDecision.strategic_recommendation_candidate_id == recommendation_id,
            StrategicDecision.superseded_at.is_(None),
        )
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.execute(query).scalar_one_or_none()

    def get_for_campaign_by_public_id(
        self, *, campaign_id: uuid.UUID, public_id: str, for_update: bool = False
    ) -> StrategicDecision | None:
        """Non-leaky, campaign-scoped resource lookup — identical discipline
        to every other bounded context's own
        ``get_for_campaign_by_public_id`` (e.g.
        ``app/commercial/repository.py``): a Decision that does not exist at
        all, that belongs to a different workspace, or that belongs to a
        different Campaign within the same workspace, is indistinguishable
        — all three return ``None`` here."""
        query = select(StrategicDecision).where(
            StrategicDecision.campaign_id == campaign_id, StrategicDecision.public_id == public_id
        )
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.execute(query).scalar_one_or_none()

    def list_for_campaign(self, campaign_id: uuid.UUID) -> list[StrategicDecision]:
        return list(
            self.session.execute(
                select(StrategicDecision)
                .where(StrategicDecision.campaign_id == campaign_id)
                .order_by(StrategicDecision.created_at.asc(), StrategicDecision.id.asc())
            )
            .scalars()
            .all()
        )
