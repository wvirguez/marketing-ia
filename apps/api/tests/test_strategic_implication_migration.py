"""Reproducible round trip against a dedicated, disposable migration
database — mirrors ``tests/test_learning_qualification_migration.py``
exactly, one migration later."""
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from app.core.config import get_settings
from app.persistence.testing import assert_safe_test_database_url

pytestmark = pytest.mark.postgres


def test_strategic_implication_migration_round_trip(monkeypatch):
    url = os.environ.get("TEST_MIGRATIONS_DATABASE_URL")
    if not url:
        pytest.skip("TEST_MIGRATIONS_DATABASE_URL requires a separate disposable PostgreSQL database")
    assert_safe_test_database_url(url)
    assert url != os.environ.get("TEST_DATABASE_URL"), "Migration DB must be isolated from application tests"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    engine = create_engine(url)
    config = Config("alembic.ini")
    try:
        # Re-runnable only in the explicitly test-scoped database above.
        if "strategic_implications" in inspect(engine).get_table_names():
            command.downgrade(config, "c6b92e815f40")
        command.upgrade(config, "c6b92e815f40")
        assert "strategic_implications" not in inspect(engine).get_table_names()
        for cycle in range(2):
            command.upgrade(config, "head")
            inspector = inspect(engine)

            from app.learning.models import StrategicImplication, StrategicRecommendationCandidate
            table = StrategicImplication.__table__
            columns = inspector.get_columns(table.name)
            assert {c["name"] for c in columns} == set(table.columns.keys())
            assert {c["name"]: c["nullable"] for c in columns} == {c.name: c.nullable for c in table.columns}
            assert "status" not in {c["name"] for c in columns}
            assert "updated_at" not in {c["name"] for c in columns}
            fks = inspector.get_foreign_keys(table.name)
            assert {tuple(fk["constrained_columns"]) for fk in fks} == {
                tuple(c.name for c in fk.columns) for fk in table.foreign_key_constraints
            }
            assert all(fk["options"].get("ondelete") != "CASCADE" for fk in fks)
            assert all(len(i["name"]) <= 63 for i in inspector.get_indexes(table.name))
            unique = {tuple(sorted(u["column_names"])) for u in inspector.get_unique_constraints(table.name)}
            assert tuple(sorted(("id", "workspace_id"))) in unique

            src_columns = {c["name"]: c for c in inspector.get_columns("strategic_recommendation_candidates")}
            assert src_columns["strategic_implication_id"]["nullable"] is True
            src_fks = inspector.get_foreign_keys("strategic_recommendation_candidates")
            implication_fk = next(fk for fk in src_fks if "strategic_implication_id" in fk["constrained_columns"])
            assert implication_fk["referred_table"] == "strategic_implications"
            assert set(implication_fk["constrained_columns"]) == {"strategic_implication_id", "workspace_id"}
            assert len(implication_fk["name"]) <= 63

            audit_columns = {c["name"] for c in inspector.get_columns("audit_events")}
            assert "strategic_implication_id" in audit_columns

            with engine.connect() as connection:
                assert connection.scalar(text("select count(*) from strategic_implications")) == 0
                # MVP-27, MVP-28B, and MVP-29B each added one additive
                # migration after this one — "head" now means
                # eae9bb978d9c, not this migration's own revision.
                assert connection.scalar(text("select version_num from alembic_version")) == "eae9bb978d9c"

            if cycle == 0:
                # Explicit target, not a relative "-1": MVP-27 added a
                # further migration on top of this one, so "one step down"
                # from head no longer removes strategic_implications — the
                # revision immediately before it, "c6b92e815f40", still does.
                command.downgrade(config, "c6b92e815f40")
                inspector = inspect(engine)
                assert "strategic_implications" not in inspector.get_table_names()
                remaining_src_columns = {c["name"] for c in inspector.get_columns("strategic_recommendation_candidates")}
                assert "strategic_implication_id" not in remaining_src_columns
                remaining_audit_columns = {c["name"] for c in inspector.get_columns("audit_events")}
                assert "strategic_implication_id" not in remaining_audit_columns
    finally:
        engine.dispose()
        get_settings.cache_clear()
