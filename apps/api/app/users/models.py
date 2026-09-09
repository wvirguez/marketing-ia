"""User identity.

Email handling: ``email`` preserves what the user actually typed (only
whitespace-trimmed); ``normalized_email`` (lowercased, trimmed) is the
column uniqueness and lookups are enforced against — this is the
"normalize before comparing, don't silently rewrite what's shown to the
user" split BACKEND-04 asks for.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

_LOCALE_MAX_LENGTH = 35
_TIMEZONE_MAX_LENGTH = 64


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


class UserPreference(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """BACKEND-12 Governance Freeze §D/§F: per-user, cross-workspace UI
    preferences — explicitly its own row per BACKEND-01's domain-model
    catalog ("User Preferences" is listed separately from "User"), not
    columns on ``User`` itself. Global to the user, no ``workspace_id``
    (BACKEND-01: "Belongs to User", not tenant-owned). No native enum for
    ``locale``/``timezone`` — no canonical vocabulary was ever named for
    either, and inventing one would freeze a value set nobody specified.
    No ``public_id`` — never independently addressed; reached only via
    ``/api/v1/users/me``, which is already addressed by session identity.
    """

    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    locale: Mapped[str | None] = mapped_column(String(_LOCALE_MAX_LENGTH), default=None)
    timezone: Mapped[str | None] = mapped_column(String(_TIMEZONE_MAX_LENGTH), default=None)
