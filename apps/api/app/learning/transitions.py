"""The single, centralized source of truth for legal LearningCandidate
status transitions (mirroring the centralization pattern in
``app/content/transitions.py``/``app/strategy/transitions.py``/
``app/orchestration/transitions.py``). No router or repository ever
checks/applies a transition itself — every status change flows through
``app/learning/service.py``, which consults this matrix exclusively.

The complete canonical graph from
`docs/backend/BACKEND-01-ARCHITECTURE.md` §2I — every legal edge named
there is represented here, no other edge exists.
"""

from __future__ import annotations

from app.learning.models import LearningCandidateStatus

LEARNING_CANDIDATE_TRANSITIONS: dict[LearningCandidateStatus, frozenset[LearningCandidateStatus]] = {
    LearningCandidateStatus.CANDIDATE_IDENTIFIED: frozenset({LearningCandidateStatus.PROVISIONAL}),
    LearningCandidateStatus.PROVISIONAL: frozenset({LearningCandidateStatus.VALIDATION_PENDING}),
    LearningCandidateStatus.VALIDATION_PENDING: frozenset(
        {
            LearningCandidateStatus.VALIDATED,
            LearningCandidateStatus.REJECTED,
            LearningCandidateStatus.INSUFFICIENT_EVIDENCE,
        }
    ),
    LearningCandidateStatus.INSUFFICIENT_EVIDENCE: frozenset({LearningCandidateStatus.VALIDATION_PENDING}),
    # Terminal — no outgoing edges named by BACKEND-01.
    LearningCandidateStatus.VALIDATED: frozenset(),
    LearningCandidateStatus.REJECTED: frozenset(),
}


def is_legal_learning_candidate_transition(current: LearningCandidateStatus, target: LearningCandidateStatus) -> bool:
    return target in LEARNING_CANDIDATE_TRANSITIONS[current]
