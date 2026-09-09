"""Public-facing DTOs for the users domain.

Never includes ``password_hash`` or the internal UUID primary key —
only the public_id and genuinely user-facing fields.

BACKEND-12 Governance Freeze §19/§20/§W: ``extra="forbid"`` on every
PATCH request schema so an unsupported writable field (``email``,
``role``, ``status``, ``password``, ``public_id``, ...) is rejected by
validation (422) rather than silently ignored. ``model_fields_set`` (not
a plain ``is None`` check) is how the router distinguishes "omitted"
from "explicitly null" for ``preferences.locale``/``preferences.timezone``
— see ``app/users/router.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.users.models import User, UserPreference


class UserPreferencesPublic(BaseModel):
    locale: str | None
    timezone: str | None


class UserPublic(BaseModel):
    id: str
    email: str
    display_name: str
    status: str
    preferences: UserPreferencesPublic


class UserPreferencesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locale: str | None = Field(default=None, min_length=1, max_length=35)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)


class UserPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    preferences: UserPreferencesPatch | None = None

    @model_validator(mode="after")
    def _reject_null_display_name(self) -> "UserPatchRequest":
        # `display_name` is a NOT NULL column with no "clear it" concept
        # (BACKEND-12 Freeze §C) — unlike locale/timezone, explicit null
        # is never a valid value for it, only "omitted" is.
        if "display_name" in self.model_fields_set and self.display_name is None:
            raise ValueError("display_name must not be null.")
        return self


def user_to_public(user: User, *, preference: UserPreference | None) -> UserPublic:
    return UserPublic(
        id=user.public_id,
        email=user.email,
        display_name=user.display_name,
        status=user.status.value,
        preferences=UserPreferencesPublic(
            locale=preference.locale if preference is not None else None,
            timezone=preference.timezone if preference is not None else None,
        ),
    )
