from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

# Registers the `postgres_engine`/`db_session` fixtures (tests/dbtest.py)
# for every test module, without importing DB-only names into modules
# that don't need them.
pytest_plugins = ["tests.dbtest"]


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)
