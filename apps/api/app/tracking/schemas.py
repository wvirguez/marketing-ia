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
    # MVP-24: identity-only (MVP-24A-R1) — public IDs of the
    # ContentDistributions this Requirement has been declared applicable
    # to. Never implies verification, firing, or attribution.
    associated_distribution_ids: list[str] = Field(default_factory=list)


class TrackingPlanPublic(BaseModel):
    id: str
    status: TrackingReadinessStatus
    requirements: list[TrackingRequirementPublic]


class TrackingResponse(BaseModel):
    plan: TrackingPlanPublic | None


class CreateTrackingRequirementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)


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


def tracking_requirement_to_public(
    requirement: TrackingRequirement, *, associated_distribution_ids: list[str] | None = None
) -> TrackingRequirementPublic:
    return TrackingRequirementPublic(
        id=requirement.public_id, name=requirement.name, status=requirement.status,
        associated_distribution_ids=associated_distribution_ids if associated_distribution_ids is not None else [],
    )


def tracking_plan_to_public(
    plan: TrackingPlan,
    *,
    requirements: list[TrackingRequirement],
    associated_distribution_ids_by_requirement_id: dict | None = None,
) -> TrackingPlanPublic:
    by_id = associated_distribution_ids_by_requirement_id or {}
    return TrackingPlanPublic(
        id=plan.public_id,
        status=plan.status,
        requirements=[
            tracking_requirement_to_public(requirement, associated_distribution_ids=by_id.get(requirement.id, []))
            for requirement in requirements
        ],
    )
