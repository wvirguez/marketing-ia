"""CSRF protection tests (BACKEND-04 §33) — real database required."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.authtest import register_payload

pytestmark = pytest.mark.postgres


def _register_and_get_csrf(client: TestClient) -> str:
    client.post("/api/v1/auth/register", json=register_payload())
    return client.get("/api/v1/auth/csrf").json()["csrf_token"]


def test_authenticated_post_without_csrf_header_is_rejected(auth_client: TestClient) -> None:
    _register_and_get_csrf(auth_client)

    response = auth_client.post("/api/v1/auth/logout")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


def test_wrong_csrf_token_is_rejected(auth_client: TestClient) -> None:
    _register_and_get_csrf(auth_client)

    response = auth_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "completely-wrong-value"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


def test_valid_csrf_token_is_accepted(auth_client: TestClient) -> None:
    csrf_token = _register_and_get_csrf(auth_client)

    response = auth_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_token})

    assert response.status_code == 200


def test_safe_get_does_not_require_csrf(auth_client: TestClient) -> None:
    auth_client.post("/api/v1/auth/register", json=register_payload())

    response = auth_client.get("/api/v1/auth/session")  # no X-CSRF-Token header at all

    assert response.status_code == 200


def test_csrf_token_alone_cannot_replace_session_authentication(auth_client: TestClient) -> None:
    csrf_token = _register_and_get_csrf(auth_client)
    auth_client.cookies.clear()  # no session cookie at all now

    response = auth_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_token})

    # Without a session, there is nothing to check the CSRF token
    # against in the first place — authentication is required first.
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_session_cookie_alone_cannot_replace_csrf_proof(auth_client: TestClient) -> None:
    _register_and_get_csrf(auth_client)  # cookie is set; no CSRF header ever sent

    response = auth_client.post("/api/v1/auth/logout")  # cookie present, no X-CSRF-Token

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


def test_a_different_users_csrf_token_does_not_work_for_this_session(auth_client: TestClient) -> None:
    """The CSRF secret is bound to one specific AuthSession — another
    user's (validly-issued) token must not satisfy this session's check."""
    from fastapi.testclient import TestClient as _TestClient

    my_csrf = _register_and_get_csrf(auth_client)

    other_client = _TestClient(auth_client.app, raise_server_exceptions=False)
    other_client.post("/api/v1/auth/register", json=register_payload())
    other_csrf = other_client.get("/api/v1/auth/csrf").json()["csrf_token"]

    assert other_csrf != my_csrf

    response = auth_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": other_csrf})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"
