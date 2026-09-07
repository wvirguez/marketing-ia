from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import AuthSession
from app.core.ids import generate_public_id


class AuthSessionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        csrf_secret: str,
        created_at: datetime,
        expires_at: datetime,
        user_agent_summary: str | None,
    ) -> AuthSession:
        auth_session = AuthSession(
            public_id=generate_public_id("SES"),
            user_id=user_id,
            token_hash=token_hash,
            csrf_secret=csrf_secret,
            created_at=created_at,
            last_seen_at=created_at,
            expires_at=expires_at,
            user_agent_summary=user_agent_summary,
        )
        self.session.add(auth_session)
        self.session.flush()
        return auth_session

    def get_by_token_hash(self, token_hash: str) -> AuthSession | None:
        return self.session.execute(
            select(AuthSession).where(AuthSession.token_hash == token_hash)
        ).scalar_one_or_none()
