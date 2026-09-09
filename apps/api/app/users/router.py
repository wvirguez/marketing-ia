"""Users API surface. ``GET /me`` (BACKEND-04) plus, since BACKEND-12,
``PATCH /me`` for the Profile + AI-adjacent-but-actually-per-user
Settings section (display_name, locale, timezone) — see
``app/users/service.py`` for the transaction/audit contract.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_csrf
from app.persistence.session import get_db
from app.users.models import User
from app.users.repository import UserPreferenceRepository
from app.users.schemas import UserPatchRequest, UserPublic, user_to_public
from app.users.service import UNSET, UserService

router = APIRouter(tags=["users"])


@router.get("/me", response_model=UserPublic)
async def get_me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> UserPublic:
    # GET never creates a row (BACKEND-12 Freeze §17) — a plain lookup.
    preference = UserPreferenceRepository(db).get_by_user_id(user.id)
    return user_to_public(user, preference=preference)


@router.patch("/me", response_model=UserPublic, dependencies=[Depends(require_csrf)])
async def patch_me(
    payload: UserPatchRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserPublic:
    preferences_fields = payload.preferences.model_fields_set if payload.preferences is not None else set()
    user, preference = UserService(db).update_profile(
        user=user,
        display_name=payload.display_name if "display_name" in payload.model_fields_set else UNSET,
        locale=payload.preferences.locale if "locale" in preferences_fields else UNSET,
        timezone=payload.preferences.timezone if "timezone" in preferences_fields else UNSET,
    )
    return user_to_public(user, preference=preference)
