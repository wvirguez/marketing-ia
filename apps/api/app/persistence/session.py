"""Engine factory and per-request Session lifecycle.

Session contract (BACKEND-03 §5):

- One ``Session`` per request. ``get_db`` is a generator dependency, so
  FastAPI calls it fresh for every request — there is no global mutable
  Session anywhere in this module, only a stateless session *factory*
  (``sessionmaker``), which is safe to hold at module scope.
- No implicit commit. This module never calls ``session.commit()`` — a
  read-only request does nothing beyond closing its session. Only
  application/service-layer code that explicitly needs to persist a
  write may call ``session.commit()`` on the session it was given.
  SESSION ownership (opening/closing this request's connection) is a
  separate concern from TRANSACTION AUTHORITY (deciding a write should
  be durable) — this module owns only the former.
- Rollback on exception: if the request raised, whatever was pending on
  the session is rolled back before it closes, so a half-applied write
  never survives a failed request.
- Always closed: the ``finally`` block guarantees the session (and its
  underlying connection) is released back to the pool exactly once per
  request, success or failure.
- No cross-request reuse: each call to ``get_db`` creates a brand new
  ``Session`` instance from the factory.
"""

from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger("impulso.database")

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def create_db_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Conservative, development-appropriate pool defaults.

    Not tuned for production load — that is a later, separately
    authorized concern. Future tunables (see README): ``pool_size``,
    ``max_overflow``, ``pool_timeout``, ``pool_recycle``, and whether a
    connection pooler (e.g. PgBouncer) sits in front of Postgres at all.
    """
    return create_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,  # detect stale/dropped connections before handing them out
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
    )


def configure_database(database_url: str, *, echo: bool = False) -> None:
    """Creates the engine and session factory for this process.

    Called once at application startup with a concrete URL — never at
    import time with a possibly-missing one, so importing this module
    never requires a database to exist.
    """
    global _engine, _session_factory
    _engine = create_db_engine(database_url, echo=echo)
    _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def is_configured() -> bool:
    return _engine is not None


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError(
            "Database is not configured. configure_database() must be called "
            "with a DATABASE_URL before get_engine()/get_db() can be used."
        )
    return _engine


def dispose_engine() -> None:
    """Releases all pooled connections. Used by tests to guarantee a
    clean slate between isolated engines pointed at different databases;
    not called during normal request handling."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields one Session for the current request.

    Overridable in tests via ``app.dependency_overrides[get_db] = ...``.
    """
    if _session_factory is None:
        raise RuntimeError(
            "Database is not configured. configure_database() must be called "
            "with a DATABASE_URL before get_db() can be used."
        )
    session = _session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_ready() -> bool:
    """Lightweight connectivity probe for the readiness endpoint.

    Returns False (never raises) on any failure — the caller decides how
    to respond; this function's job is only to answer "can I reach the
    database right now," not to expose *why* it can't.
    """
    if _engine is None:
        return False
    try:
        with _engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("Database readiness check failed")
        return False
