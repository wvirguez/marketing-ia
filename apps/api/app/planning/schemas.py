"""Public DTOs for the planning read surface, extended by MVP-33B for the
governed write path. Never expose an internal UUID or a raw ``workspace_id``
— only public_id-derived fields (mirroring ``app/strategy/schemas.py``).

No field here ever represents approval, readiness, production authorization,
or a chain-of-thought/reasoning trace — see ``app/planning/models.py`` for
why none of those exist on the underlying models in the first place.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.planning.models import ContentPlan, PlanItem

_SUMMARY_MAX_LENGTH = 4000
_FORMAT_MAX_LENGTH = 100
_OBJECTIVE_MAX_LENGTH = 1000


class PlanItemPublic(BaseModel):
    id: str
    format: str
    objective: str
    sequence: int
    scheduled_date: date | None
    created_at: datetime


class ContentPlanPublic(BaseModel):
    id: str
    campaign_id: str
    # MVP-33B: nullable Experiment provenance, exposed as its PUBLIC ID
    # (never the internal UUID) — matching this codebase's own established
    # convention (ExperimentPublic.hypothesis_id, ContentPlanPublic's own
    # existing campaign_id) of bare `_id` naming for public-ID-valued
    # fields. NULL = Case G (generic, no Experiment claim). A non-NULL
    # value makes exactly one claim: "this ContentPlan was created to
    # operationalize Experiment E" (MVP-33A §AA) — nothing stronger.
    experiment_id: str | None
    version: int
    summary: str
    created_at: datetime


class PlanOutputResponse(BaseModel):
    plan: ContentPlanPublic | None
    items: list[PlanItemPublic]


class CreatePlanItemInput(BaseModel):
    """MVP-33B: the client-supplied shape for one optional initial
    PlanItem — mirrors ``PlanItem``'s own creation fields exactly, no
    server-controlled field accepted."""

    model_config = ConfigDict(extra="forbid")

    format: str = Field(min_length=1, max_length=_FORMAT_MAX_LENGTH)
    objective: str = Field(min_length=1, max_length=_OBJECTIVE_MAX_LENGTH)
    sequence: int
    scheduled_date: date | None = None

    @field_validator("format", "objective")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("This field cannot be blank.")
        return stripped


class CreateContentPlanRequest(BaseModel):
    """MVP-33A §G/§19/MVP-33A-R1 (frozen contract, implemented MVP-33B):
    the minimum client assertion for a governed ContentPlan — a summary,
    an optional Experiment provenance claim, and optional initial
    PlanItems. No ``campaign_run_id``, ``stage_execution_id``, ``origin``,
    ``version``, ``status``, ``workspace_id``, ``campaign_id``, ``actor``,
    ``actor_user_id``, ``strategy_id``, ``positioning_id``, or Variant
    field is ever accepted from the client — all server-derived or
    server-controlled."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=_SUMMARY_MAX_LENGTH)
    experiment_public_id: str | None = None
    items: list[CreatePlanItemInput] | None = None

    @field_validator("summary")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("This field cannot be blank.")
        return stripped


def content_plan_to_public(
    plan: ContentPlan, *, campaign_public_id: str, experiment_public_id: str | None = None
) -> ContentPlanPublic:
    return ContentPlanPublic(
        id=plan.public_id,
        campaign_id=campaign_public_id,
        experiment_id=experiment_public_id,
        version=plan.version,
        summary=plan.summary,
        created_at=plan.created_at,
    )


def plan_item_to_public(item: PlanItem) -> PlanItemPublic:
    return PlanItemPublic(
        id=item.public_id,
        format=item.format,
        objective=item.objective,
        sequence=item.sequence,
        scheduled_date=item.scheduled_date,
        created_at=item.created_at,
    )
