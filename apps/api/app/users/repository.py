"""Data access for the users domain.

Never calls ``session.commit()`` — see BACKEND-03's session contract
(``app/persistence/session.py``): commits belong to the service/router
layer that owns a unit of work, never to a low-level repository.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import generate_public_id
from app.users.models import User, UserStatus


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_normalized_email(self, normalized_email: str) -> User | None:
        return self.session.execute(
            select(User).where(User.normalized_email == normalized_email)
        ).scalar_one_or_none()

    def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return self.session.get(User, user_id)

    def create(self, *, email: str, normalized_email: str, password_hash: str, display_name: str) -> User:
        user = User(
            public_id=generate_public_id("USR"),
            email=email,
            normalized_email=normalized_email,
            password_hash=password_hash,
            display_name=display_name,
            status=UserStatus.ACTIVE,
        )
        self.session.add(user)
        self.session.flush()
        return user
