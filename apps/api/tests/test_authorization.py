"""`require_role` — the reusable role-check dependency factory
(BACKEND-04 §21). Not wired to any route yet (no role-gated endpoint
exists in this stage), so tested directly as a plain function.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.core.api_errors import ForbiddenError
from app.auth.dependencies import require_role
from app.workspaces.models import Membership, MembershipRole, MembershipStatus


def _membership(role: MembershipRole) -> Membership:
    return Membership(
        public_id="MBR-TESTFIXTURE01",
        user_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        role=role,
        status=MembershipStatus.ACTIVE,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def test_allows_membership_with_matching_role() -> None:
    dependency = require_role(MembershipRole.OWNER, MembershipRole.ADMIN)
    membership = _membership(MembershipRole.ADMIN)

    assert dependency(membership) is membership


def test_rejects_membership_without_matching_role() -> None:
    dependency = require_role(MembershipRole.OWNER)
    membership = _membership(MembershipRole.MEMBER)

    with pytest.raises(ForbiddenError):
        dependency(membership)


def test_single_role_shorthand() -> None:
    dependency = require_role(MembershipRole.MEMBER)
    membership = _membership(MembershipRole.MEMBER)

    assert dependency(membership) is membership
