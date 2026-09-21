"""Reproducible round trip against a dedicated, disposable migration database."""
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from app.core.config import get_settings
from app.persistence.testing import assert_safe_test_database_url

pytestmark = pytest.mark.postgres


def test_qualification_migration_round_trip(monkeypatch):
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
        if "learning_qualifications" in inspect(engine).get_table_names():
            command.downgrade(config, "5d213770c589")
        command.upgrade(config, "5d213770c589")
        assert "learning_qualifications" not in inspect(engine).get_table_names()
        for cycle in range(2):
            command.upgrade(config, "head")
            inspector = inspect(engine)
            from app.learning.models import LearningQualification, LearningQualificationSignal
            for model in (LearningQualification, LearningQualificationSignal):
                table = model.__table__
                columns = inspector.get_columns(table.name)
                assert {c["name"] for c in columns} == set(table.columns.keys())
                assert {c["name"]: c["nullable"] for c in columns} == {c.name: c.nullable for c in table.columns}
                assert "consistency_status" not in {c["name"] for c in columns}
                fks = inspector.get_foreign_keys(table.name)
                assert {tuple(fk["constrained_columns"]) for fk in fks} == {tuple(c.name for c in fk.columns) for fk in table.foreign_key_constraints}
                assert all(fk["options"].get("ondelete") != "CASCADE" for fk in fks)
                assert all(len(i["name"]) <= 63 for i in inspector.get_indexes(table.name))
            unique = inspector.get_unique_constraints("learning_qualifications")
            assert any(u["column_names"] == ["learning_candidate_id"] for u in unique)
            assert len(inspector.get_check_constraints("learning_qualification_signals")) == 3
            indexes = {i["name"]: i for i in inspector.get_indexes("learning_qualification_signals")}
            for name in ("uq_lqs_active_pair", "uq_lqs_effective_pair"):
                assert indexes[name]["unique"] and indexes[name]["dialect_options"]["postgresql_where"]
            enums = {e["name"] for e in inspector.get_enums()}
            assert len([n for n in enums if n.startswith("learning_qualification_")]) == 4
            with engine.connect() as connection:
                assert connection.scalar(text("select count(*) from learning_qualifications")) == 0
                # MVP-26, MVP-27, MVP-28B, MVP-29B, and MVP-30B each added
                # one additive migration after this one — "head" now means
                # b27209ee89a1, not this migration's own revision.
                assert connection.scalar(text("select version_num from alembic_version")) == "7c1e9a4d2b68"  # Experiment Evidence Binding: current head, bumped from 53b4bd83a005
            if cycle == 0:
                # Explicit target, not a relative "-1": MVP-26 added a
                # further migration on top of this one, so "one step down"
                # from head no longer removes learning_qualifications —
                # the revision immediately before it, "5d213770c589", still
                # does.
                command.downgrade(config, "5d213770c589")
                inspector = inspect(engine)
                assert "learning_qualifications" not in inspector.get_table_names()
                assert "learning_qualification_signals" not in inspector.get_table_names()
                assert not [e for e in inspector.get_enums() if e["name"].startswith("learning_qualification_")]
    finally:
        engine.dispose()
        get_settings.cache_clear()
