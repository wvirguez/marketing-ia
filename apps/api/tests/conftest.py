from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

# Registers shared fixtures for every test module, without importing
# DB-only names into modules that don't need them.
pytest_plugins = ["tests.dbtest", "tests.authtest", "tests.campaignstest", "tests.orchestrationtest", "tests.researchtest"]


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)
