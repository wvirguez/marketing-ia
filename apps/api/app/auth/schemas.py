from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from app.users.schemas import UserPublic
from app.workspaces.schemas import MembershipPublic, WorkspacePublic

# Deliberately not using Pydantic's EmailStr: it requires the optional
# `email-validator` dependency, which is not among BACKEND-04's approved
# packages. This regex is intentionally permissive (it rejects obvious
# non-emails, not every RFC 5322 edge case) — real deliverability is a
# concern for a verification-email flow this stage does not implement.
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email_shape(value: str) -> str:
    value = value.strip()
    if not _EMAIL_PATTERN.match(value):
        raise ValueError("Enter a valid email address.")
    return value


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(min_length=1, max_length=255)
    organization_name: str | None = Field(default=None, max_length=255)
    workspace_name: str | None = Field(default=None, max_length=255)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str) -> str:
        return _validate_email_shape(value)


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str) -> str:
        return _validate_email_shape(value)


class SessionContext(BaseModel):
    user: UserPublic
    workspace: WorkspacePublic
    membership: MembershipPublic


class CsrfTokenResponse(BaseModel):
    csrf_token: str


class LogoutResponse(BaseModel):
    status: str = "logged_out"
