"""Transaction foundation tests (BACKEND-03 §15).

All marked `postgres` — they require a real, reachable, test-scoped
PostgreSQL database (see `tests/dbtest.py`) and are skipped, not faked,
when one is not configured.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.persistence.probe import PersistenceProbe
from app.persistence.session import configure_database, dispose_engine, get_db

pytestmark = pytest.mark.postgres


def test_session_opens_and_closes_without_error(db_session) -> None:
    assert db_session.execute(select(PersistenceProbe)).all() == []
    # Reaching this line without the fixture raising already proves the
    # session opened; the fixture's own teardown proves it closes.


def test_rollback_after_exception_discards_pending_write(db_session) -> None:
    db_session.add(PersistenceProbe(label="will-be-rolled-back"))

    class _DeliberateFailure(Exception):
        pass

    with pytest.raises(_DeliberateFailure):
        try:
            db_session.flush()  # sends the INSERT, does not commit
            raise _DeliberateFailure("simulated failure mid-request")
        except _DeliberateFailure:
            db_session.rollback()
            raise

    rows = db_session.execute(select(PersistenceProbe)).all()
    assert rows == []


def test_committed_data_is_visible_within_the_test_transaction(db_session) -> None:
    db_session.add(PersistenceProbe(label="committed-row"))
    db_session.commit()

    # A fresh query (not the same in-memory object) proves this is a real
    # round trip through the database, not just Python-side state.
    row = db_session.execute(
        select(PersistenceProbe).where(PersistenceProbe.label == "committed-row")
    ).scalar_one()
    assert row.label == "committed-row"
    assert row.created_at is not None


def test_rolled_back_data_does_not_persist(db_session) -> None:
    db_session.add(PersistenceProbe(label="never-committed"))
    db_session.flush()
    db_session.rollback()

    rows = db_session.execute(
        select(PersistenceProbe).where(PersistenceProbe.label == "never-committed")
    ).all()
    assert rows == []


def test_get_db_yields_a_new_session_each_call(postgres_engine: Engine) -> None:
    configure_database(
        postgres_engine.url.render_as_string(hide_password=False)
    )
    try:
        first_generator = get_db()
        first_session = next(first_generator)
        first_generator.close()

        second_generator = get_db()
        second_session = next(second_generator)
        second_generator.close()

        assert first_session is not second_session
    finally:
        dispose_engine()


def test_get_db_rolls_back_and_closes_when_the_request_raises(postgres_engine: Engine) -> None:
    configure_database(
        postgres_engine.url.render_as_string(hide_password=False)
    )
    try:
        generator = get_db()
        session = next(generator)
        session.add(PersistenceProbe(label="should-not-survive-request-failure"))
        session.flush()  # sends the INSERT; still uncommitted

        with pytest.raises(RuntimeError, match="simulated request failure"):
            generator.throw(RuntimeError("simulated request failure"))

        # The session is closed by `get_db`'s `finally` — using it again
        # would itself prove nothing was left half-open. Prove the actual
        # contract instead: query with a completely separate connection
        # and confirm the flushed-but-never-committed row never became
        # visible to anyone else.
        with postgres_engine.connect() as verification_connection:
            from sqlalchemy import text

            count = verification_connection.execute(
                text(
                    "SELECT count(*) FROM _infra_persistence_probe "
                    "WHERE label = 'should-not-survive-request-failure'"
                )
            ).scalar_one()
        assert count == 0
    finally:
        dispose_engine()
