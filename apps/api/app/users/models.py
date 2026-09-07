"""User identity.

Email handling: ``email`` preserves what the user actually typed (only
whitespace-trimmed); ``normalized_email`` (lowercased, trimmed) is the
column uniqueness and lookups are enforced against — this is the
"normalize before comparing, don't silently rewrite what's shown to the
user" split BACKEND-04 asks for.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320))
    normalized_email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name="user_status", native_enum=True),
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
    )


def normalize_email(email: str) -> str:
    return email.strip().lower()
