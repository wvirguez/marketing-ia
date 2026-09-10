"""The single, centralized source of truth for legal TrackingPlan status
transitions (mirroring the centralization pattern in
``app/content/transitions.py``/``app/learning/transitions.py``). No router
or repository ever checks/applies a transition itself — every status
change flows through ``app/tracking/service.py``, which consults this
matrix exclusively.

The complete canonical graph from
`docs/backend/BACKEND-01-ARCHITECTURE.md` §2G — every legal edge named
there is represented here, no other edge exists.
"""

from __future__ import annotations

from app.tracking.models import TrackingReadinessStatus

TRACKING_PLAN_TRANSITIONS: dict[TrackingReadinessStatus, frozenset[TrackingReadinessStatus]] = {
    TrackingReadinessStatus.NOT_DEFINED: frozenset({TrackingReadinessStatus.REQUIREMENTS_DEFINED}),
    TrackingReadinessStatus.REQUIREMENTS_DEFINED: frozenset({TrackingReadinessStatus.CONFIGURATION_PENDING}),
    TrackingReadinessStatus.CONFIGURATION_PENDING: frozenset(
        {TrackingReadinessStatus.CONFIGURED, TrackingReadinessStatus.FAILED_VERIFICATION}
    ),
    TrackingReadinessStatus.CONFIGURED: frozenset({TrackingReadinessStatus.VERIFICATION_PENDING}),
    TrackingReadinessStatus.VERIFICATION_PENDING: frozenset(
        {TrackingReadinessStatus.CERTIFIED, TrackingReadinessStatus.FAILED_VERIFICATION}
    ),
    TrackingReadinessStatus.FAILED_VERIFICATION: frozenset({TrackingReadinessStatus.CONFIGURATION_PENDING}),
    # Terminal — no outgoing edge named by BACKEND-01.
    TrackingReadinessStatus.CERTIFIED: frozenset(),
}


def is_legal_tracking_plan_transition(current: TrackingReadinessStatus, target: TrackingReadinessStatus) -> bool:
    return target in TRACKING_PLAN_TRANSITIONS[current]


# --- Requirement mutation lifecycle (Governance Freeze-R, TRK-D36/TRK-D37) --

# record_tracking_requirement: legal only while the parent Plan has not yet
# asserted "configuration complete" or beyond — adding a brand-new,
# never-configured-or-verified row after CONFIGURED/VERIFICATION_PENDING
# would silently invalidate either assertion; CERTIFIED is terminal.
REQUIREMENT_CREATION_ALLOWED_STATUSES: frozenset[TrackingReadinessStatus] = frozenset(
    {
        TrackingReadinessStatus.NOT_DEFINED,
        TrackingReadinessStatus.REQUIREMENTS_DEFINED,
        TrackingReadinessStatus.CONFIGURATION_PENDING,
        TrackingReadinessStatus.FAILED_VERIFICATION,
    }
)

# update_tracking_requirement_status: legal in every state except CERTIFIED
# — a per-row descriptive status update does not change the set's
# membership and is the expected mechanism for recording per-requirement
# verification progress up through VERIFICATION_PENDING. CERTIFIED alone
# freezes it, preserving a stable, terminal attestation (TRK-D38).
REQUIREMENT_STATUS_MUTATION_ALLOWED_STATUSES: frozenset[TrackingReadinessStatus] = frozenset(
    {
        TrackingReadinessStatus.NOT_DEFINED,
        TrackingReadinessStatus.REQUIREMENTS_DEFINED,
        TrackingReadinessStatus.CONFIGURATION_PENDING,
        TrackingReadinessStatus.CONFIGURED,
        TrackingReadinessStatus.VERIFICATION_PENDING,
        TrackingReadinessStatus.FAILED_VERIFICATION,
    }
)
