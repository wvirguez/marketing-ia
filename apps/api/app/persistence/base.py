"""Declarative base, metadata naming convention, and reusable mixins for
every future ORM model.

No business/domain tables are defined against this ``Base`` in
BACKEND-03 — see ``app.persistence.probe`` for the one narrowly-scoped,
explicitly non-domain table this stage needs to prove Alembic and the
session/transaction contract actually work against a real database.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint/index names, so Alembic's autogenerate produces
# stable, reviewable migrations instead of driver-assigned names that
# differ between runs and environments.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    metadata = metadata


class UUIDPrimaryKeyMixin:
    """Internal database primary key convention from BACKEND-01: a UUID
    primary key, separate from any future human-readable public id
    (``CMP-...``, ``USR-...``, etc. — the public id belongs on each
    domain model itself, not this shared mixin, since its prefix is
    entity-specific).

    BACKEND-01 specifies **UUIDv7** for its time-ordered index locality.
    This mixin currently generates UUIDv4 instead: UUIDv7 has no stdlib
    generator before Python 3.14 (this project targets 3.11–3.13), and no
    UUIDv7 library is among BACKEND-03's approved dependencies. This is a
    deliberate, reported gap — see the BACKEND-03 delivery notes — not a
    silent deviation. Swap ``default=uuid.uuid4`` for a UUIDv7 generator
    once Python 3.14 is the floor or a UUIDv7 dependency is explicitly
    authorized; nothing else about this mixin needs to change.
    """

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """``created_at``/``updated_at``, stamped by the database server
    (``func.now()``), not the application clock — so the value is
    consistent regardless of which process/host wrote the row."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
