"""The single, centralized source of truth for legal Content Piece and
Content Approval status transitions (mirroring the centralization pattern
in ``app/orchestration/transitions.py``/``app/strategy/transitions.py``).
No router or repository ever checks/applies a transition itself — every
status change flows through ``app/content/service.py``, which consults
these matrices exclusively.

Both graphs are the *complete* canonical graphs from
`docs/backend/BACKEND-01-ARCHITECTURE.md` §2D/§2E — every legal edge BACKEND-01
names is represented here, even edges BACKEND-10 does not yet expose a
service method for (``READY_FOR_REVIEW -> APPROVED``,
``APPROVED -> READY_FOR_DISTRIBUTION``, ``READY_FOR_DISTRIBUTION ->
DISTRIBUTED``, and the ``EXPIRED`` edges). LEGAL TRANSITION != SERVICE
METHOD AUTHORIZED: ``app/content/service.py`` is the only place that
decides which of these edges a caller may actually trigger, and through
which named method — this module only answers "is X -> Y a legal edge at
all," never "is anyone allowed to cause it right now."
"""

from __future__ import annotations

from app.content.models import ContentApprovalStatus, ContentPieceStatus

CONTENT_PIECE_TRANSITIONS: dict[ContentPieceStatus, frozenset[ContentPieceStatus]] = {
    ContentPieceStatus.DRAFT: frozenset({ContentPieceStatus.IN_PRODUCTION}),
    ContentPieceStatus.IN_PRODUCTION: frozenset({ContentPieceStatus.PRODUCED}),
    ContentPieceStatus.PRODUCED: frozenset({ContentPieceStatus.READY_FOR_REVIEW}),
    ContentPieceStatus.READY_FOR_REVIEW: frozenset(
        {
            ContentPieceStatus.REVISION_REQUESTED,
            ContentPieceStatus.APPROVED,
            ContentPieceStatus.ARCHIVED,
        }
    ),
    ContentPieceStatus.REVISION_REQUESTED: frozenset({ContentPieceStatus.IN_PRODUCTION}),
    ContentPieceStatus.APPROVED: frozenset(
        {ContentPieceStatus.READY_FOR_DISTRIBUTION, ContentPieceStatus.ARCHIVED}
    ),
    ContentPieceStatus.READY_FOR_DISTRIBUTION: frozenset({ContentPieceStatus.DISTRIBUTED}),
    # Terminal — no outgoing edges named by BACKEND-01.
    ContentPieceStatus.DISTRIBUTED: frozenset(),
    ContentPieceStatus.ARCHIVED: frozenset(),
}


def is_legal_content_piece_transition(current: ContentPieceStatus, target: ContentPieceStatus) -> bool:
    return target in CONTENT_PIECE_TRANSITIONS[current]


CONTENT_APPROVAL_TRANSITIONS: dict[ContentApprovalStatus, frozenset[ContentApprovalStatus]] = {
    ContentApprovalStatus.REQUESTED: frozenset(
        {ContentApprovalStatus.UNDER_REVIEW, ContentApprovalStatus.EXPIRED}
    ),
    ContentApprovalStatus.UNDER_REVIEW: frozenset(
        {
            ContentApprovalStatus.APPROVED,
            ContentApprovalStatus.CHANGES_REQUESTED,
            ContentApprovalStatus.REJECTED,
            ContentApprovalStatus.EXPIRED,
        }
    ),
    # Terminal — no outgoing edges named by BACKEND-01.
    ContentApprovalStatus.APPROVED: frozenset(),
    ContentApprovalStatus.CHANGES_REQUESTED: frozenset(),
    ContentApprovalStatus.REJECTED: frozenset(),
    ContentApprovalStatus.EXPIRED: frozenset(),
}


def is_legal_content_approval_transition(current: ContentApprovalStatus, target: ContentApprovalStatus) -> bool:
    return target in CONTENT_APPROVAL_TRANSITIONS[current]
