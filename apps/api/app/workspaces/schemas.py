"""Public-facing DTOs for the workspaces domain, including (BACKEND-12)
the Settings response/request shapes.

BACKEND-12 Governance Freeze §L/§7: ``tone``/``depth``/``creativity`` are
validated against a conservative, bounded, machine-safe pattern —
lowercase letters/digits/underscores, starting with a letter — never a
native enum (no canonical vocabulary exists to freeze) and never a
Spanish frontend literal copied verbatim. §I/§10: notification keys are
restricted to ``NOTIFICATION_KEYS``; ``extra="forbid"`` rejects any other
key at the schema boundary; explicit ``null`` for a supplied notification
key is rejected (booleans only).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.workspaces.models import (
    NOTIFICATION_DEFAULTS,
    AIPreference,
    Membership,
    NotificationPreference,
    Workspace,
)

_AI_PREFERENCE_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"


class WorkspacePublic(BaseModel):
    id: str
    name: str
    slug: str


class MembershipPublic(BaseModel):
    role: str


def workspace_to_public(workspace: Workspace) -> WorkspacePublic:
    return WorkspacePublic(id=workspace.public_id, name=workspace.name, slug=workspace.slug)


def membership_to_public(membership: Membership) -> MembershipPublic:
    return MembershipPublic(role=membership.role.value)


# --- Settings (BACKEND-12) --------------------------------------------


class WorkspaceProfilePublic(BaseModel):
    name: str


class AIPreferencesPublic(BaseModel):
    tone: str | None
    depth: str | None
    creativity: str | None


class NotificationsPublic(BaseModel):
    campaign_ready: bool
    content_review: bool
    metrics_available: bool
    analysis_complete: bool
    weekly_summary: bool


class WorkspaceSettingsResponse(BaseModel):
    workspace: WorkspaceProfilePublic
    ai_preferences: AIPreferencesPublic
    notifications: NotificationsPublic


class WorkspaceProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)


class AIPreferencesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tone: str | None = Field(default=None, pattern=_AI_PREFERENCE_PATTERN)
    depth: str | None = Field(default=None, pattern=_AI_PREFERENCE_PATTERN)
    creativity: str | None = Field(default=None, pattern=_AI_PREFERENCE_PATTERN)


class NotificationsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # `strict=True`: Pydantic v2's default *lax* bool parsing coerces
    # strings like "yes"/"true"/"1" into real booleans, which would
    # silently accept exactly the non-boolean input BACKEND-12 Freeze
    # §10/§16 requires rejected — a supplied value must be a genuine
    # JSON boolean, never a string/int that merely looks like one.
    campaign_ready: bool | None = Field(default=None, strict=True)
    content_review: bool | None = Field(default=None, strict=True)
    metrics_available: bool | None = Field(default=None, strict=True)
    analysis_complete: bool | None = Field(default=None, strict=True)
    weekly_summary: bool | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def _reject_null_values(self) -> "NotificationsPatch":
        # A key that IS supplied must be a real boolean — omission is
        # the only way to "not change" a toggle (BACKEND-12 Freeze §16:
        # "Notification booleans: NULL NOT ALLOWED").
        for key in self.model_fields_set:
            if getattr(self, key) is None:
                raise ValueError(f"{key} must be a boolean, not null.")
        return self


class WorkspaceSettingsPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: WorkspaceProfilePatch | None = None
    ai_preferences: AIPreferencesPatch | None = None
    notifications: NotificationsPatch | None = None


def workspace_settings_to_public(
    workspace: Workspace,
    ai_preference: AIPreference | None,
    notification_preference: NotificationPreference | None,
) -> WorkspaceSettingsResponse:
    stored_toggles = notification_preference.toggles if notification_preference is not None else {}
    projected = dict(NOTIFICATION_DEFAULTS)
    projected.update(stored_toggles)
    return WorkspaceSettingsResponse(
        workspace=WorkspaceProfilePublic(name=workspace.name),
        ai_preferences=AIPreferencesPublic(
            tone=ai_preference.tone if ai_preference is not None else None,
            depth=ai_preference.depth if ai_preference is not None else None,
            creativity=ai_preference.creativity if ai_preference is not None else None,
        ),
        notifications=NotificationsPublic(**projected),
    )
