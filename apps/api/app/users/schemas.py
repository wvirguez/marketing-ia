"""Public-facing DTOs for the users domain.

Never includes ``password_hash`` or the internal UUID primary key —
only the public_id and genuinely user-facing fields.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.users.models import User


class UserPublic(BaseModel):
    id: str
    email: str
    display_name: str
    status: str


def user_to_public(user: User) -> UserPublic:
    return UserPublic(id=user.public_id, email=user.email, display_name=user.display_name, status=user.status.value)
