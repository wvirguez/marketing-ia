"""Public DTOs for the Tracking read/mutate surface. Never expose an
internal UUID, a raw ``workspace_id``, or a raw campaign UUID — only
public_id-derived fields (mirroring ``app/learning/schemas.py``'s own
convention).

No field here ever represents provider identifiers, pixel/event type
taxonomy, credential material, or a chain-of-thought/reasoning trace
(Governance Freeze §M/§AC).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from app.tracking.models import TrackingPlan, TrackingReadinessStatus, TrackingRequirement


class TrackingRequirementPublic(BaseModel):
    id: str
    name: str
    status: str | None


class TrackingPlanPublic(BaseModel):
    id: str
    status: TrackingReadinessStatus
    requirements: list[TrackingRequirementPublic]


class TrackingResponse(BaseModel):
    plan: TrackingPlanPublic | None


class TransitionPlanOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["TRANSITION_PLAN"]
    target_status: TrackingReadinessStatus


class UpdateRequirementStatusOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["UPDATE_REQUIREMENT_STATUS"]
    requirement_id: str
    status: str | None = None


TrackingPatchRequest = Annotated[
    Union[TransitionPlanOperation, UpdateRequirementStatusOperation],
    Field(discriminator="operation"),
]


def tracking_requirement_to_public(requirement: TrackingRequirement) -> TrackingRequirementPublic:
    return TrackingRequirementPublic(id=requirement.public_id, name=requirement.name, status=requirement.status)


def tracking_plan_to_public(plan: TrackingPlan, *, requirements: list[TrackingRequirement]) -> TrackingPlanPublic:
    return TrackingPlanPublic(
        id=plan.public_id,
        status=plan.status,
        requirements=[tracking_requirement_to_public(requirement) for requirement in requirements],
    )
