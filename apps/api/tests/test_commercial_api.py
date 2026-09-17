"""API contract, tenancy, and campaign-scope tests for the Commercial
surface (MVP-27B). All marked `postgres`."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.campaignstest import campaign_payload, register_and_get_csrf

pytestmark = pytest.mark.postgres


def _objectives_path(fixtures: dict) -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/commercial-objectives"


def _offers_path(fixtures: dict) -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/offers"


def _headers(fixtures: dict) -> dict:
    return {"X-CSRF-Token": fixtures["csrf_token"]}


# --- route surface ---------------------------------------------------------


def test_only_get_post_routes_exist_put_and_delete_rejected(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    for path in (_objectives_path(fixtures), _offers_path(fixtures)):
        assert fixtures["client"].get(path).status_code == 200
        assert fixtures["client"].put(path, json={}).status_code == 405
        assert fixtures["client"].delete(path).status_code == 405


def test_empty_list_for_campaign_with_no_commercial_entities(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    assert fixtures["client"].get(_objectives_path(fixtures)).json() == []
    assert fixtures["client"].get(_offers_path(fixtures)).json() == []


# --- independent create: never replaces, never primary ---------------------


def test_create_objective_returns_201_and_frozen_shape(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(
        _objectives_path(fixtures), json={"statement": "Generate qualified leads."}, headers=_headers(fixtures)
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["statement"] == "Generate qualified leads."
    assert body["current"] is True
    assert body["superseded_at"] is None
    assert body["superseded_by_commercial_objective_id"] is None
    assert body["campaign_id"] == fixtures["campaign_id"]


def test_create_offer_returns_201_and_frozen_shape(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(
        _offers_path(fixtures),
        json={"statement": "Six-week course.", "price": "199.00", "currency": "usd"},
        headers=_headers(fixtures),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["statement"] == "Six-week course."
    assert body["price"] == "199.00" or float(body["price"]) == 199.00
    assert body["currency"] == "USD"  # normalized from lowercase input
    assert body["current"] is True


def test_two_independent_creates_both_remain_current(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    first = fixtures["client"].post(_objectives_path(fixtures), json={"statement": "Leads."}, headers=_headers(fixtures))
    second = fixtures["client"].post(_objectives_path(fixtures), json={"statement": "Revenue."}, headers=_headers(fixtures))
    assert first.status_code == 201 and second.status_code == 201
    listing = fixtures["client"].get(_objectives_path(fixtures)).json()
    assert len(listing) == 2
    assert all(item["current"] is True for item in listing)


def test_offer_price_currency_pair_violation_returns_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(
        _offers_path(fixtures), json={"statement": "x", "price": "10.00"}, headers=_headers(fixtures)
    )
    assert response.status_code == 422


def test_offer_negative_price_returns_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(
        _offers_path(fixtures), json={"statement": "x", "price": "-1.00", "currency": "USD"}, headers=_headers(fixtures)
    )
    assert response.status_code == 422


def test_offer_malformed_currency_returns_422(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(
        _offers_path(fixtures), json={"statement": "x", "price": "10.00", "currency": "US"}, headers=_headers(fixtures)
    )
    assert response.status_code == 422


# --- supersede: distinct action from create, one-shot ----------------------


def test_supersede_objective_creates_replacement_and_marks_original_historical(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    created = fixtures["client"].post(_objectives_path(fixtures), json={"statement": "Leads."}, headers=_headers(fixtures)).json()
    replaced = fixtures["client"].post(
        f"{_objectives_path(fixtures)}/{created['id']}/supersede", json={"statement": "Revenue instead."}, headers=_headers(fixtures)
    )
    assert replaced.status_code == 201, replaced.text
    replacement_body = replaced.json()
    assert replacement_body["current"] is True
    assert replacement_body["id"] != created["id"]

    listing = {item["id"]: item for item in fixtures["client"].get(_objectives_path(fixtures)).json()}
    assert listing[created["id"]]["current"] is False
    assert listing[created["id"]]["superseded_by_commercial_objective_id"] == replacement_body["id"]
    assert listing[replacement_body["id"]]["current"] is True


def test_supersede_offer_creates_replacement_and_marks_original_historical(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    created = fixtures["client"].post(
        _offers_path(fixtures), json={"statement": "Course.", "price": "199.00", "currency": "USD"}, headers=_headers(fixtures)
    ).json()
    replaced = fixtures["client"].post(
        f"{_offers_path(fixtures)}/{created['id']}/supersede",
        json={"statement": "Course, new price.", "price": "249.00", "currency": "USD"},
        headers=_headers(fixtures),
    )
    assert replaced.status_code == 201, replaced.text
    replacement_body = replaced.json()

    listing = {item["id"]: item for item in fixtures["client"].get(_offers_path(fixtures)).json()}
    assert listing[created["id"]]["current"] is False
    assert listing[created["id"]]["superseded_by_offer_id"] == replacement_body["id"]


def test_second_supersede_attempt_on_the_same_objective_returns_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    created = fixtures["client"].post(_objectives_path(fixtures), json={"statement": "Leads."}, headers=_headers(fixtures)).json()
    first = fixtures["client"].post(
        f"{_objectives_path(fixtures)}/{created['id']}/supersede", json={"statement": "First replacement."}, headers=_headers(fixtures)
    )
    assert first.status_code == 201
    second = fixtures["client"].post(
        f"{_objectives_path(fixtures)}/{created['id']}/supersede", json={"statement": "Second attempt."}, headers=_headers(fixtures)
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "COMMERCIAL_OBJECTIVE_ALREADY_SUPERSEDED"


def test_second_supersede_attempt_on_the_same_offer_returns_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    created = fixtures["client"].post(_offers_path(fixtures), json={"statement": "x"}, headers=_headers(fixtures)).json()
    first = fixtures["client"].post(
        f"{_offers_path(fixtures)}/{created['id']}/supersede", json={"statement": "First replacement."}, headers=_headers(fixtures)
    )
    assert first.status_code == 201
    second = fixtures["client"].post(
        f"{_offers_path(fixtures)}/{created['id']}/supersede", json={"statement": "Second attempt."}, headers=_headers(fixtures)
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "OFFER_ALREADY_SUPERSEDED"


# --- authority / CSRF -------------------------------------------------------


def test_create_without_csrf_token_is_rejected(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(_objectives_path(fixtures), json={"statement": "x"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


def test_supersede_without_csrf_token_is_rejected(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    created = fixtures["client"].post(_objectives_path(fixtures), json={"statement": "x"}, headers=_headers(fixtures)).json()
    response = fixtures["client"].post(f"{_objectives_path(fixtures)}/{created['id']}/supersede", json={"statement": "y"})
    assert response.status_code == 403


# --- tenancy: non-leaky cross-workspace resolution --------------------------


def test_objective_supersede_rejects_cross_workspace_target(auth_client: TestClient) -> None:
    """An Objective belonging to a wholly different Workspace is
    non-leakily rejected exactly like an unknown public_id — mirrors
    ``test_learning_maturation_api.py::test_recommendation_rejects_cross_workspace_implication``."""
    client_a = auth_client
    csrf_a = register_and_get_csrf(client_a, display_name="User A")
    campaign_a_id = client_a.post(
        "/api/v1/campaigns", json=campaign_payload(name="A"), headers={"X-CSRF-Token": csrf_a}
    ).json()["campaign"]["id"]

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    csrf_b = register_and_get_csrf(client_b, display_name="User B")
    campaign_b_id = client_b.post(
        "/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}
    ).json()["campaign"]["id"]
    objective_b = client_b.post(
        f"/api/v1/campaigns/{campaign_b_id}/commercial-objectives",
        json={"statement": "Workspace B's own objective."}, headers={"X-CSRF-Token": csrf_b},
    ).json()

    response = client_a.post(
        f"/api/v1/campaigns/{campaign_a_id}/commercial-objectives/{objective_b['id']}/supersede",
        json={"statement": "Cross-workspace substitution attempt."}, headers={"X-CSRF-Token": csrf_a},
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_offer_supersede_rejects_cross_workspace_target(auth_client: TestClient) -> None:
    client_a = auth_client
    csrf_a = register_and_get_csrf(client_a, display_name="User A2")
    campaign_a_id = client_a.post(
        "/api/v1/campaigns", json=campaign_payload(name="A2"), headers={"X-CSRF-Token": csrf_a}
    ).json()["campaign"]["id"]

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    csrf_b = register_and_get_csrf(client_b, display_name="User B2")
    campaign_b_id = client_b.post(
        "/api/v1/campaigns", json=campaign_payload(name="B2"), headers={"X-CSRF-Token": csrf_b}
    ).json()["campaign"]["id"]
    offer_b = client_b.post(
        f"/api/v1/campaigns/{campaign_b_id}/offers", json={"statement": "Workspace B's own offer."}, headers={"X-CSRF-Token": csrf_b}
    ).json()

    response = client_a.post(
        f"/api/v1/campaigns/{campaign_a_id}/offers/{offer_b['id']}/supersede",
        json={"statement": "Cross-workspace substitution attempt."}, headers={"X-CSRF-Token": csrf_a},
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_nonexistent_public_id_supersede_returns_403_not_404(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(
        f"{_objectives_path(fixtures)}/OBJ-DOESNOTEXIST/supersede", json={"statement": "x"}, headers=_headers(fixtures)
    )
    assert response.status_code == 403
