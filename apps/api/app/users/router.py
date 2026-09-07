from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.dependencies import get_current_user
from app.users.models import User
from app.users.schemas import UserPublic, user_to_public

router = APIRouter(tags=["users"])


@router.get("/me", response_model=UserPublic)
async def get_me(user: User = Depends(get_current_user)) -> UserPublic:
    return user_to_public(user)
