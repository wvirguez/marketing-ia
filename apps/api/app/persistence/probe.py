"""BACKEND-03 persistence infrastructure probe.

``PersistenceProbe`` is **not a domain/business entity**. It exists only
so this stage has a real, mapped table to migrate, insert into, commit,
and roll back — proving the Alembic setup and the session/transaction
contract actually work against a real PostgreSQL database, without
prematurely creating User/Workspace/Campaign/Content/etc. tables (those
belong to their own, separately authorized stages per
``docs/backend/BACKEND-01-ARCHITECTURE.md``).

It doubles as a live example of the base model conventions from
BACKEND-03 §6: a UUID primary key (see the caveat on
``UUIDPrimaryKeyMixin`` about UUIDv4 vs. UUIDv7) and server-stamped
``created_at``/``updated_at`` timestamps. It deliberately has no
``workspace_id`` — it is not tenant-owned data.

This table (and this module) is expected to be removed once BACKEND-05
introduces the first real domain tables and no longer needs a
stand-in to prove the persistence foundation works.
"""

from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PersistenceProbe(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "_infra_persistence_probe"

    label: Mapped[str] = mapped_column()
