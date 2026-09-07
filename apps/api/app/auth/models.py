"""Opaque server-side session record.

SESSION TOKEN != USER ID: the browser only ever holds a random, opaque,
high-entropy token in an HttpOnly cookie — never the user's id, public
or internal. The raw token is never written to this table; only
``token_hash`` (a one-way digest) is stored, so a stolen database
backup cannot be used to forge a session (see ``app/auth/security.py``
for why a fast digest, not Argon2id, is the right tool here).

``csrf_secret`` is deliberately plaintext (not hashed) — see the
rationale in ``app/auth/csrf.py``, where the same value is both issued
to the frontend and compared against on each mutation. Its threat model
differs from the session token: knowing it alone grants nothing without
also having the session cookie.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, UUIDPrimaryKeyMixin


class AuthSession(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "auth_sessions"

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)

    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_secret: Mapped[str] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Set at creation only in this stage — live-updating it on every
    # authenticated request is a deferred refinement (it would require
    # `get_current_session` to write, breaking the "reads don't commit"
    # session contract for what is otherwise a pure-read dependency).
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # A short, truncated summary only — never the full User-Agent string,
    # and never an IP address (BACKEND-04 §10 explicitly excludes IP
    # history without a clearly justified need, and none exists yet).
    user_agent_summary: Mapped[str | None] = mapped_column(String(120), nullable=True)
