"""The single, centralized source of truth for legal CampaignRun and
RunStageExecution state transitions (BACKEND-06 §8/§11/§28). No router
or repository ever checks/applies a transition itself — every lifecycle
change flows through ``app/orchestration/service.py``, which consults
these matrices exclusively.

Run transitions mirror BACKEND-01 state machine B exactly
(`docs/backend/BACKEND-01-ARCHITECTURE.md` §2B) — this stage adds no new
CampaignRunStatus value and no new edge beyond what BACKEND-01 already
specifies for Orchestration Run. The illustrative transition list in the
BACKEND-06 request (``READY``, ``PAUSED``, ``WAITING_FOR_INPUT`` as Run
states) is explicitly subordinate to BACKEND-01's own already-defined
machine per that request's own instruction ("do not assume this list
overrides an existing BACKEND-01 state machine") — those names are not
introduced here for Run; ``WAITING_FOR_INPUT`` exists only as a Stage
state, and BACKEND-01's own ``AWAITING_HUMAN_DECISION`` is what a run
uses instead.
"""

from __future__ import annotations

from app.campaigns.models import CampaignRunStatus
from app.orchestration.models import StageExecutionStatus

RUN_TRANSITIONS: dict[CampaignRunStatus, frozenset[CampaignRunStatus]] = {
    CampaignRunStatus.CREATED: frozenset(
        {CampaignRunStatus.RUNNING, CampaignRunStatus.FAILED, CampaignRunStatus.CANCELLED}
    ),
    CampaignRunStatus.RUNNING: frozenset(
        {
            CampaignRunStatus.AWAITING_HUMAN_DECISION,
            CampaignRunStatus.COMPLETED,
            CampaignRunStatus.FAILED,
            CampaignRunStatus.CANCELLED,
        }
    ),
    CampaignRunStatus.AWAITING_HUMAN_DECISION: frozenset(
        {CampaignRunStatus.RUNNING, CampaignRunStatus.FAILED, CampaignRunStatus.CANCELLED}
    ),
    CampaignRunStatus.COMPLETED: frozenset(),
    CampaignRunStatus.FAILED: frozenset(),
    CampaignRunStatus.CANCELLED: frozenset(),
}


def is_legal_run_transition(current: CampaignRunStatus, target: CampaignRunStatus) -> bool:
    return target in RUN_TRANSITIONS[current]


STAGE_TRANSITIONS: dict[StageExecutionStatus, frozenset[StageExecutionStatus]] = {
    StageExecutionStatus.PENDING: frozenset(
        {StageExecutionStatus.READY, StageExecutionStatus.SKIPPED, StageExecutionStatus.CANCELLED}
    ),
    StageExecutionStatus.READY: frozenset(
        {StageExecutionStatus.RUNNING, StageExecutionStatus.SKIPPED, StageExecutionStatus.CANCELLED}
    ),
    StageExecutionStatus.RUNNING: frozenset(
        {
            StageExecutionStatus.WAITING_FOR_INPUT,
            StageExecutionStatus.BLOCKED,
            StageExecutionStatus.COMPLETED,
            StageExecutionStatus.FAILED,
            StageExecutionStatus.CANCELLED,
        }
    ),
    StageExecutionStatus.WAITING_FOR_INPUT: frozenset(
        {StageExecutionStatus.RUNNING, StageExecutionStatus.CANCELLED}
    ),
    StageExecutionStatus.BLOCKED: frozenset(
        {StageExecutionStatus.READY, StageExecutionStatus.RUNNING, StageExecutionStatus.CANCELLED}
    ),
    # Terminal — no outgoing edges. A terminal stage must never silently
    # transition back to an active state (BACKEND-06 §11).
    StageExecutionStatus.COMPLETED: frozenset(),
    StageExecutionStatus.FAILED: frozenset(),
    StageExecutionStatus.SKIPPED: frozenset(),
    StageExecutionStatus.CANCELLED: frozenset(),
}


def is_legal_stage_transition(current: StageExecutionStatus, target: StageExecutionStatus) -> bool:
    return target in STAGE_TRANSITIONS[current]
