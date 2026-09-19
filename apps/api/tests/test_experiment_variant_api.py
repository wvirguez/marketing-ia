"""API contract, pinning, Definition-lock, idempotency, eligibility, audit,
tenancy, read-model, non-effects and firewall tests for Governed Variant
Identity (MVP-38, implementing the frozen MVP-38A/-38B contract). All marked
`postgres`.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.audit.models import ActorType, AuditEvent
from app.persistence.session import get_engine
from app.strategy.models import Experiment, ExperimentDefinitionVersion, ExperimentVariant, Hypothesis, HypothesisStatus
from app.users.models import User
from app.workspaces.models import Membership, MembershipRole, MembershipStatus
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.settingstest import add_member_to_workspace, login_as
from tests.test_experiment_api import _build_base_strategy, _build_hypothesis, _experiments_path, _post
from tests.test_experiment_definition_api import (
    _declare,
    _delta,
    _experiment,
    _history,
    _payload,
    _supersede_strategy,
    _table_counts,
)

pytestmark = pytest.mark.postgres

FORBIDDEN_FIELDS = [
    "role", "weight", "traffic", "traffic_percentage", "metric", "success_criterion", "status", "allocation",
    "exposure", "winner", "result", "created_by", "ordinal", "experiment_id", "workspace_id", "control", "treatment",
]


def _variants_path(fixtures: dict, experiment_id: str, campaign_id: str | None = None) -> str:
    return f"/api/v1/campaigns/{campaign_id or fixtures['campaign_id']}/experiments/{experiment_id}/variants"


def _vpayload(version_id: str, **overrides: object) -> dict:
    body: dict = {
        "definition_version_id": version_id,
        "label": "Condition A",
        "condition_description": "The opening line is phrased as a direct question.",
        "client_request_id": uuid.uuid4().hex,
    }
    body.update(overrides)
    return body


def _declare_variant(fixtures: dict, experiment_id: str, body: dict):
    return _post(fixtures, _variants_path(fixtures, experiment_id), body)


def _list(fixtures: dict, experiment_id: str, **params: object):
    return fixtures["client"].get(_variants_path(fixtures, experiment_id), params=params)


def _defined_experiment(fixtures: dict) -> tuple[str, str, str, str]:
    """An Experiment with a declared Definition v1: (strategy, hypothesis, experiment, EXD id)."""
    strategy_id, hypothesis_id, experiment_id = _experiment(fixtures)
    version = _declare(fixtures, experiment_id)
    assert version.status_code == 201, version.text
    return strategy_id, hypothesis_id, experiment_id, version.json()["id"]


def _strategy_tip(fixtures: dict, experiment_id: str) -> dict:
    experiments = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").json()["experiments"]
    return next(e for e in experiments if e["id"] == experiment_id)


def _variant_audit(experiment_public_id: str) -> list[dict]:
    with OrmSession(get_engine()) as session:
        experiment = session.execute(select(Experiment).where(Experiment.public_id == experiment_public_id)).scalar_one()
        rows = session.execute(
            select(AuditEvent)
            .where(AuditEvent.experiment_id == experiment.id, AuditEvent.event_type == "strategy.variant.declared")
            .order_by(AuditEvent.created_at, AuditEvent.id)
        ).scalars().all()
        return [
            {
                "previous_state": r.previous_state, "new_state": r.new_state, "actor_type": r.actor_type,
                "actor_user_id": r.actor_user_id, "workspace_id": r.workspace_id, "campaign_id": r.campaign_id,
                "strategy_id": r.strategy_id, "hypothesis_id": r.hypothesis_id, "experiment_id": r.experiment_id,
                "version_id": r.experiment_definition_version_id, "variant_id": r.experiment_variant_id,
                "request_id": r.request_id,
            }
            for r in rows
        ]


# --- creation / shape / list -----------------------------------------------------------


def test_creation_returns_201_and_the_frozen_public_shape(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    response = _declare_variant(fixtures, experiment_id, _vpayload(version_id))
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["id"].startswith("VAR-") and len(data["id"]) == 16
    assert data["experiment_id"] == experiment_id
    assert data["definition_version_id"] == version_id
    assert data["ordinal"] == 1
    assert data["label"] == "Condition A"
    assert set(data) == {
        "id", "experiment_id", "definition_version_id", "ordinal", "label", "condition_description", "created_at",
    }
    # Public ids only: no internal UUID is exposed.
    assert not [v for v in data.values() if isinstance(v, str) and len(v) == 36 and v.count("-") == 4]


def test_ordinals_are_sequential_and_the_list_is_ordered_by_ordinal(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    for label in ("First", "Second", "Third"):
        assert _declare_variant(fixtures, experiment_id, _vpayload(version_id, label=label)).status_code == 201
    page = _list(fixtures, experiment_id).json()
    assert [(i["ordinal"], i["label"]) for i in page["items"]] == [(1, "First"), (2, "Second"), (3, "Third")]
    assert page["total"] == 3 and page["limit"] == 20 and page["offset"] == 0
    assert page["experiment_id"] == experiment_id


def test_there_is_no_variant_maximum(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    for index in range(25):  # more than the rejected 38A proposal of 20
        response = _declare_variant(fixtures, experiment_id, _vpayload(version_id, label=f"Condition {index}"))
        assert response.status_code == 201, (index, response.text)
    assert _list(fixtures, experiment_id).json()["total"] == 25


def test_pagination_limits_and_offsets_are_deterministic(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    for index in range(5):
        assert _declare_variant(fixtures, experiment_id, _vpayload(version_id, label=f"C{index}")).status_code == 201
    first = _list(fixtures, experiment_id, limit=2, offset=0).json()
    second = _list(fixtures, experiment_id, limit=2, offset=2).json()
    third = _list(fixtures, experiment_id, limit=2, offset=4).json()
    assert [i["label"] for i in first["items"]] == ["C0", "C1"]
    assert [i["label"] for i in second["items"]] == ["C2", "C3"]
    assert [i["label"] for i in third["items"]] == ["C4"]
    assert first["total"] == second["total"] == third["total"] == 5
    assert second["limit"] == 2 and second["offset"] == 2
    assert _list(fixtures, experiment_id, limit=2, offset=10).json()["items"] == []
    # Repeating a page returns exactly the same page.
    assert _list(fixtures, experiment_id, limit=2, offset=2).json() == second
    for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "x"}):
        assert _list(fixtures, experiment_id, **bad).status_code == 422, bad
    assert _list(fixtures, experiment_id, limit=100).status_code == 200


def test_list_for_a_definition_less_experiment_is_an_empty_page(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    body = _list(fixtures, experiment_id).json()
    assert body == {"experiment_id": experiment_id, "items": [], "limit": 20, "offset": 0, "total": 0}


# --- current-tip pinning / Definition lock ---------------------------------------------


def test_only_the_current_tip_can_be_pinned_and_a_historical_pin_is_a_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, v1 = _defined_experiment(fixtures)
    v2 = _declare(fixtures, experiment_id, _payload(base_version=1, changed_factor="Second factor"))
    assert v2.status_code == 201
    before = _table_counts()
    stale = _declare_variant(fixtures, experiment_id, _vpayload(v1))
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "EXPERIMENT_VARIANT_DEFINITION_VERSION_NOT_CURRENT"
    assert _delta(before, _table_counts()) == {}
    ok = _declare_variant(fixtures, experiment_id, _vpayload(v2.json()["id"]))
    assert ok.status_code == 201 and ok.json()["definition_version_id"] == v2.json()["id"]


def test_the_server_never_silently_substitutes_the_tip(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, v1 = _defined_experiment(fixtures)
    assert _declare(fixtures, experiment_id, _payload(base_version=1, changed_factor="Second")).status_code == 201
    response = _declare_variant(fixtures, experiment_id, _vpayload(v1))
    assert response.status_code == 409
    assert _list(fixtures, experiment_id).json()["total"] == 0
    body = _vpayload(v1)
    del body["definition_version_id"]
    assert _declare_variant(fixtures, experiment_id, body).status_code == 422  # the pin is mandatory


def test_first_variant_pins_the_definition_and_blocks_a_revision(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, v1 = _defined_experiment(fixtures)
    # Zero Variants: the Definition is still revisable.
    assert _history(fixtures, experiment_id).json()["versions"][0]["is_pinned"] is False
    assert _declare(fixtures, experiment_id, _payload(base_version=1, changed_factor="Revisable")).status_code == 201
    v2 = _history(fixtures, experiment_id).json()["versions"][-1]["id"]

    assert _declare_variant(fixtures, experiment_id, _vpayload(v2)).status_code == 201
    before = _table_counts()
    blocked = _declare(fixtures, experiment_id, _payload(base_version=2, changed_factor="Too late"))
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "EXPERIMENT_DEFINITION_PINNED"
    assert "VAR-" not in blocked.text  # the pinning child is not named
    assert _delta(before, _table_counts()) == {}
    # BASE_STALE still wins over PINNED for a stale base, and PINNED wins over UNCHANGED.
    stale = _declare(fixtures, experiment_id, _payload(base_version=1, changed_factor="Stale"))
    assert stale.json()["error"]["code"] == "EXPERIMENT_DEFINITION_BASE_STALE"
    tip = _history(fixtures, experiment_id).json()["versions"][-1]
    same = _declare(
        fixtures, experiment_id,
        _payload(base_version=2, changed_factor="Revisable"),
    )
    assert same.status_code == 409 and same.json()["error"]["code"] == "EXPERIMENT_DEFINITION_PINNED"
    assert tip["version"] == 2


def test_a_matching_definition_replay_is_still_a_200_after_the_definition_is_pinned(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    body = _payload()
    first = _declare(fixtures, experiment_id, body)
    assert first.status_code == 201
    assert _declare_variant(fixtures, experiment_id, _vpayload(first.json()["id"])).status_code == 201
    replay = _declare(fixtures, experiment_id, body)
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["is_pinned"] is True and replay.json()["variant_count"] == 1


def test_variant_count_and_is_pinned_are_derived_everywhere_the_definition_appears(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    tip = _strategy_tip(fixtures, experiment_id)["definition"]
    assert (tip["variant_count"], tip["is_pinned"]) == (0, False)
    for index in range(3):
        assert _declare_variant(fixtures, experiment_id, _vpayload(version_id, label=f"L{index}")).status_code == 201
    tip = _strategy_tip(fixtures, experiment_id)["definition"]
    assert (tip["variant_count"], tip["is_pinned"]) == (3, True)
    history = _history(fixtures, experiment_id).json()["versions"][0]
    assert (history["variant_count"], history["is_pinned"]) == (3, True)
    # Derived, never stored: the version row carries no such column.
    assert not {"variant_count", "is_pinned", "is_locked"} & set(ExperimentDefinitionVersion.__table__.columns.keys())


def test_a_historical_versions_pin_state_is_coherent_in_history(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, _ = _defined_experiment(fixtures)
    assert _declare(fixtures, experiment_id, _payload(base_version=1, changed_factor="Second")).status_code == 201
    v2 = _history(fixtures, experiment_id).json()["versions"][1]["id"]
    assert _declare_variant(fixtures, experiment_id, _vpayload(v2)).status_code == 201
    states = [(v["version"], v["variant_count"], v["is_pinned"]) for v in _history(fixtures, experiment_id).json()["versions"]]
    assert states == [(1, 0, False), (2, 1, True)]


# --- idempotency ------------------------------------------------------------------------


def test_same_key_same_request_is_a_200_replay_with_no_new_rows(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    body = _vpayload(version_id)
    first = _declare_variant(fixtures, experiment_id, body)
    assert first.status_code == 201
    before = _table_counts()
    replay = _declare_variant(fixtures, experiment_id, body)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert _delta(before, _table_counts()) == {}
    assert len(_variant_audit(experiment_id)) == 1


def test_same_key_with_a_different_material_request_is_an_idempotency_conflict(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    body = _vpayload(version_id)
    assert _declare_variant(fixtures, experiment_id, body).status_code == 201
    before = _table_counts()
    for variant in (
        {**body, "label": "Other label"},
        {**body, "condition_description": "A different description."},
        {**body, "label": "condition a"},  # case-sensitive material equality
    ):
        response = _declare_variant(fixtures, experiment_id, variant)
        assert response.status_code == 409 and response.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT", variant
    assert _delta(before, _table_counts()) == {}


def test_same_key_pinned_to_a_different_version_is_a_conflict(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, v1 = _defined_experiment(fixtures)
    v2 = _declare(fixtures, experiment_id, _payload(base_version=1, changed_factor="Second")).json()["id"]
    body = _vpayload(v2)
    assert _declare_variant(fixtures, experiment_id, body).status_code == 201
    response = _declare_variant(fixtures, experiment_id, {**body, "definition_version_id": v1})
    assert response.status_code == 409 and response.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_key_reused_for_another_experiment_is_a_conflict(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    versions = []
    experiments = []
    for description in ("one", "two"):
        experiment_id = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": description}).json()["id"]
        experiments.append(experiment_id)
        versions.append(_declare(fixtures, experiment_id).json()["id"])
    body = _vpayload(versions[0])
    assert _declare_variant(fixtures, experiments[0], body).status_code == 201
    response = _declare_variant(fixtures, experiments[1], {**body, "definition_version_id": versions[1]})
    assert response.status_code == 409 and response.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert experiments[0] not in response.text
    assert _list(fixtures, experiments[1]).json()["total"] == 0


def test_the_key_is_workspace_scoped_not_global(auth_client: TestClient) -> None:
    key = uuid.uuid4().hex
    statuses = []
    for name in ("Tenant One", "Tenant Two"):
        client = TestClient(auth_client.app, raise_server_exceptions=False)
        register_and_get_csrf(client, display_name=name)
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        campaign_id = client.post("/api/v1/campaigns", json=campaign_payload(name=name), headers={"X-CSRF-Token": csrf}).json()["campaign"]["id"]
        fixtures = {"client": client, "csrf_token": csrf, "campaign_id": campaign_id}
        _, _, experiment_id, version_id = _defined_experiment(fixtures)
        statuses.append(_declare_variant(fixtures, experiment_id, _vpayload(version_id, client_request_id=key)).status_code)
    assert statuses == [201, 201]


# --- label duplicates -------------------------------------------------------------------


def test_exact_and_normalized_duplicate_labels_are_a_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    assert _declare_variant(fixtures, experiment_id, _vpayload(version_id, label="Variant A")).status_code == 201
    before = _table_counts()
    for duplicate in ("Variant A", "variant a", "  Variant    A  ", "VARIANT\tA", "Variant  a"):
        response = _declare_variant(fixtures, experiment_id, _vpayload(version_id, label=duplicate))
        assert response.status_code == 409 and response.json()["error"]["code"] == "EXPERIMENT_VARIANT_LABEL_DUPLICATE", duplicate
    assert _delta(before, _table_counts()) == {}
    assert len(_variant_audit(experiment_id)) == 1
    # A genuinely different label is fine, and the stored label keeps the user's case and inner spacing.
    ok = _declare_variant(fixtures, experiment_id, _vpayload(version_id, label="  Variant   B  "))
    assert ok.status_code == 201 and ok.json()["label"] == "Variant   B"


def test_the_same_label_is_allowed_on_a_different_experiment(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    for description in ("one", "two"):
        experiment_id = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": description}).json()["id"]
        version_id = _declare(fixtures, experiment_id).json()["id"]
        assert _declare_variant(fixtures, experiment_id, _vpayload(version_id, label="Same")).status_code == 201


# --- Strategy eligibility ----------------------------------------------------------------


def test_superseded_strategy_rejects_new_variants_but_allows_reads_and_matching_replay(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id, _, experiment_id, version_id = _defined_experiment(fixtures)
    body = _vpayload(version_id)
    first = _declare_variant(fixtures, experiment_id, body)
    assert first.status_code == 201

    _supersede_strategy(fixtures, strategy_id)

    before = _table_counts()
    stale = _declare_variant(fixtures, experiment_id, _vpayload(version_id, label="Later"))
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "EXPERIMENT_VARIANT_STRATEGY_STALE"
    assert _delta(before, _table_counts()) == {}
    listing = _list(fixtures, experiment_id)
    assert listing.status_code == 200 and listing.json()["total"] == 1
    replay = _declare_variant(fixtures, experiment_id, body)
    assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"]
    assert _delta(before, _table_counts()) == {}


def test_hypothesis_status_does_not_gate_variant_creation(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, hypothesis_id, experiment_id, version_id = _defined_experiment(fixtures)
    with OrmSession(get_engine()) as session:
        hypothesis = session.execute(select(Hypothesis).where(Hypothesis.public_id == hypothesis_id)).scalar_one()
        hypothesis.status = HypothesisStatus.REFUTED
        session.commit()
    assert _declare_variant(fixtures, experiment_id, _vpayload(version_id)).status_code == 201


# --- tenancy ------------------------------------------------------------------------------


def test_unresolvable_versions_and_definition_less_experiments_are_a_non_leaky_403(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    _, _, other_experiment = _experiment_in_same_campaign(fixtures)
    other_version = _declare(fixtures, other_experiment).json()["id"]
    _, _, bare_experiment = _experiment_in_same_campaign(fixtures)  # never declared
    before = _table_counts()
    for target, pin in (
        (experiment_id, "EXD-TOTALLYFAKE0"),
        (experiment_id, other_version),  # a real EXD of ANOTHER Experiment
        (bare_experiment, version_id),  # a definition-less Experiment
        ("EXP-TOTALLYFAKE0", version_id),
    ):
        response = _declare_variant(fixtures, target, _vpayload(pin))
        assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN", (target, pin)
    assert _list(fixtures, "EXP-TOTALLYFAKE0").status_code == 403
    assert _delta(before, _table_counts()) == {}


def _experiment_in_same_campaign(fixtures: dict) -> tuple[str, str, str]:
    """A second Experiment under the campaign's current Strategy hypothesis chain."""
    strategy = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").json()
    hypothesis_id = strategy["hypotheses"][0]["id"]
    created = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "another experiment"})
    assert created.status_code == 201, created.text
    return strategy["strategy"]["id"], hypothesis_id, created.json()["id"]


def test_cross_workspace_and_cross_campaign_are_forbidden(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")
    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    campaign_a = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A"), headers={"X-CSRF-Token": csrf_a}).json()["campaign"]["id"]
    campaign_a2 = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A2"), headers={"X-CSRF-Token": csrf_a}).json()["campaign"]["id"]
    fixtures_a = {"client": client_a, "csrf_token": csrf_a, "campaign_id": campaign_a}
    _, _, experiment_a, version_a = _defined_experiment(fixtures_a)

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    campaign_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()["campaign"]["id"]
    fixtures_b = {"client": client_b, "csrf_token": csrf_b, "campaign_id": campaign_b}

    before = _table_counts()
    for campaign_id in (campaign_b, campaign_a):
        write = _post(fixtures_b, _variants_path(fixtures_b, experiment_a, campaign_id), _vpayload(version_a))
        assert write.status_code == 403 and write.json()["error"]["code"] == "FORBIDDEN"
        read = client_b.get(_variants_path(fixtures_b, experiment_a, campaign_id))
        assert read.status_code == 403 and read.json()["error"]["code"] == "FORBIDDEN"
    write = _post(fixtures_a, _variants_path(fixtures_a, experiment_a, campaign_a2), _vpayload(version_a))
    assert write.status_code == 403
    assert client_a.get(_variants_path(fixtures_a, experiment_a, campaign_a2)).status_code == 403
    assert _delta(before, _table_counts()) == {}


# --- authority -----------------------------------------------------------------------------


@pytest.mark.parametrize("role", [MembershipRole.ADMIN, MembershipRole.MEMBER])
def test_admin_and_member_can_declare_and_read(campaign_run_client: dict, role: MembershipRole) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=role)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}
    assert _declare_variant(member_fixtures, experiment_id, _vpayload(version_id)).status_code == 201
    assert _list(member_fixtures, experiment_id).status_code == 200


def test_revoked_membership_is_forbidden(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    me = fixtures["client"].get("/api/v1/users/me").json()
    with OrmSession(get_engine()) as session:
        user = session.execute(select(User).where(User.public_id == me["id"])).scalar_one()
        membership = session.execute(select(Membership).where(Membership.user_id == user.id)).scalar_one()
        membership.status = MembershipStatus.REVOKED
        session.commit()
    write = _declare_variant(fixtures, experiment_id, _vpayload(version_id))
    assert write.status_code == 403 and write.json()["error"]["code"] == "FORBIDDEN"
    assert _list(fixtures, experiment_id).status_code == 403


def test_unauthenticated_csrf_and_unsupported_methods(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    anonymous = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    assert anonymous.post(_variants_path(fixtures, experiment_id), json=_vpayload(version_id)).status_code == 401
    assert anonymous.get(_variants_path(fixtures, experiment_id)).status_code == 401
    no_csrf = fixtures["client"].post(_variants_path(fixtures, experiment_id), json=_vpayload(version_id))
    assert no_csrf.status_code == 403 and no_csrf.json()["error"]["code"] == "CSRF_INVALID"
    assert _list(fixtures, experiment_id).json()["total"] == 0  # the CSRF failure wrote nothing
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    for method in ("patch", "put", "delete"):
        assert getattr(fixtures["client"], method)(_variants_path(fixtures, experiment_id), headers=headers).status_code == 405


# --- validation ------------------------------------------------------------------------------


def test_label_bounds_and_character_rules(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    for label in ("", "   ", "x" * 201, "line\none", "line\rone", "nul\x00byte", None, 5):
        response = _declare_variant(fixtures, experiment_id, _vpayload(version_id, label=label))
        assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR", repr(label)
    ok = _declare_variant(fixtures, experiment_id, _vpayload(version_id, label=" " + "x" * 200 + " "))
    assert ok.status_code == 201 and ok.json()["label"] == "x" * 200


def test_condition_description_bounds_and_character_rules(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    for description in ("", "   ", "x" * 1001, "nul\x00byte", None):
        response = _declare_variant(fixtures, experiment_id, _vpayload(version_id, condition_description=description))
        assert response.status_code == 422, repr(description)
    multiline = _declare_variant(
        fixtures, experiment_id, _vpayload(version_id, label="M", condition_description="  line one\n  line two  ")
    )
    assert multiline.status_code == 201
    listed = _list(fixtures, experiment_id).json()["items"][0]
    assert listed["condition_description"] == "line one\n  line two"  # outer trim only; inner newline preserved
    ok = _declare_variant(fixtures, experiment_id, _vpayload(version_id, label="Max", condition_description="d" * 1000))
    assert ok.status_code == 201


def test_key_and_pin_field_validation_and_missing_fields(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    for key in ("", "k" * 101, "nul\x00key", None):
        assert _declare_variant(fixtures, experiment_id, _vpayload(version_id, client_request_id=key)).status_code == 422, repr(key)
    assert _declare_variant(fixtures, experiment_id, _vpayload(version_id, client_request_id="k" * 100)).status_code == 201
    for pin in ("", "   ", "x" * 21, "bad\x00id", None):
        assert _declare_variant(fixtures, experiment_id, _vpayload(pin, label=f"P{pin}")).status_code == 422, repr(pin)
    for field in _vpayload(version_id):
        body = _vpayload(version_id)
        del body[field]
        assert _declare_variant(fixtures, experiment_id, body).status_code == 422, field


@pytest.mark.parametrize("field", FORBIDDEN_FIELDS)
def test_forbidden_fields_are_rejected(campaign_run_client: dict, field: str) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id, version_id = _defined_experiment(fixtures)
    response = _declare_variant(fixtures, experiment_id, _vpayload(version_id, **{field: "anything"}))
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- audit -----------------------------------------------------------------------------------


def test_exactly_one_user_audit_event_per_committed_variant(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, hypothesis_id, experiment_id, version_id = _defined_experiment(fixtures)
    body = _vpayload(version_id, label="Audited")
    first = _declare_variant(fixtures, experiment_id, body)
    _declare_variant(fixtures, experiment_id, body)  # replay: none
    _declare_variant(fixtures, experiment_id, _vpayload(version_id, label="audited"))  # duplicate: none
    _declare_variant(fixtures, experiment_id, _vpayload(version_id, label=""))  # invalid: none
    _declare_variant(fixtures, experiment_id, {**body, "label": "Different"})  # key conflict: none
    second = _declare_variant(fixtures, experiment_id, _vpayload(version_id, label="Second audited"))

    events = _variant_audit(experiment_id)
    assert len(events) == 2
    assert all((e["previous_state"], e["new_state"]) == (None, "pinned:v1") for e in events)
    assert all(e["actor_type"] is ActorType.USER and e["actor_user_id"] is not None and e["request_id"] for e in events)
    with OrmSession(get_engine()) as session:
        variants = {v.public_id: v.id for v in session.execute(select(ExperimentVariant)).scalars()}
        version = session.execute(
            select(ExperimentDefinitionVersion).where(ExperimentDefinitionVersion.public_id == version_id)
        ).scalar_one()
        experiment = session.execute(select(Experiment).where(Experiment.public_id == experiment_id)).scalar_one()
        hypothesis = session.execute(select(Hypothesis).where(Hypothesis.public_id == hypothesis_id)).scalar_one()
        assert [e["variant_id"] for e in events] == [variants[first.json()["id"]], variants[second.json()["id"]]]
        assert all(
            e["version_id"] == version.id and e["experiment_id"] == experiment.id and e["hypothesis_id"] == hypothesis.id
            and e["strategy_id"] == hypothesis.strategy_id and e["workspace_id"] == experiment.workspace_id
            and e["campaign_id"] is not None
            for e in events
        )
    with OrmSession(get_engine()) as session:
        # No lock / freeze / authorization audit event exists.
        types = {t for (t,) in session.execute(select(AuditEvent.event_type).where(AuditEvent.experiment_id == experiment.id))}
        assert not [t for t in types if any(w in t for w in ("lock", "freeze", "pinned", "authoriz"))]


# --- non-effects / firewalls ---------------------------------------------------------------


def test_variant_creation_touches_only_the_variant_and_audit_tables(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, hypothesis_id, experiment_id, version_id = _defined_experiment(fixtures)

    def semantic_state() -> tuple:
        with OrmSession(get_engine()) as session:
            experiment = session.execute(select(Experiment).where(Experiment.public_id == experiment_id)).scalar_one()
            hypothesis = session.execute(select(Hypothesis).where(Hypothesis.public_id == hypothesis_id)).scalar_one()
            version = session.execute(
                select(ExperimentDefinitionVersion).where(ExperimentDefinitionVersion.public_id == version_id)
            ).scalar_one()
            return (
                experiment.status, experiment.description, experiment.updated_at,
                hypothesis.status, hypothesis.statement, hypothesis.updated_at,
                version.id, version.version, version.created_at, version.comparison_question,
                version.changed_factor, list(version.controlled_factors), version.comparison_type,
            )

    state_before = semantic_state()
    before = _table_counts()
    body = _vpayload(version_id, label="First")
    assert _declare_variant(fixtures, experiment_id, body).status_code == 201
    assert _delta(before, _table_counts()) == {"experiment_variants": 1, "audit_events": 1}

    before = _table_counts()
    assert _declare_variant(fixtures, experiment_id, body).status_code == 200  # replay
    assert _declare_variant(fixtures, experiment_id, {**body, "label": "Elsewhere"}).status_code == 409  # key conflict
    assert _declare_variant(fixtures, experiment_id, _vpayload(version_id, label="FIRST")).status_code == 409  # duplicate
    assert _declare_variant(fixtures, experiment_id, _vpayload("EXD-TOTALLYFAKE0")).status_code == 403
    assert _declare_variant(fixtures, experiment_id, _vpayload(version_id, label="")).status_code == 422
    assert _delta(before, _table_counts()) == {}
    assert semantic_state() == state_before  # Experiment, Hypothesis and the Definition row are untouched


def test_variants_make_no_experimental_claim_and_never_change_the_derived_label(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, first_experiment = _experiment(fixtures)
    for comparison_type, factors, expected in (
        ("OBSERVATIONAL", [], "DECLARED_OBSERVATIONAL_INTENT"),
        ("CONTROLLED", ["Format"], "DECLARED_CONTROLLED_INTENT"),
    ):
        experiment_id = first_experiment if comparison_type == "OBSERVATIONAL" else _experiment_in_same_campaign(fixtures)[2]
        version = _declare(fixtures, experiment_id, _payload(comparison_type=comparison_type, controlled_factors=factors))
        assert version.status_code == 201
        assert _strategy_tip(fixtures, experiment_id)["comparison_label"] == expected
        for index in range(3):
            assert _declare_variant(fixtures, experiment_id, _vpayload(version.json()["id"], label=f"V{index}")).status_code == 201
            tip = _strategy_tip(fixtures, experiment_id)
            assert tip["comparison_label"] == expected  # unchanged by the Variant count
            assert tip["status"] == "RECORDED"
            for word in ("VALID", "CAUSAL", "WINNER", "RESULT", "READY"):
                assert word not in tip["comparison_label"]
        assert tip["definition"]["is_pinned"] is True
    hypotheses = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").json()["hypotheses"]
    assert all(h["status"] == "OPEN" for h in hypotheses)
    # No persisted role / allocation / measurement / exposure / result / validity field exists.
    assert set(ExperimentVariant.__table__.columns.keys()) == {
        "id", "public_id", "workspace_id", "experiment_id", "definition_version_id", "ordinal", "label",
        "condition_description", "client_request_id", "created_at",
    }


# --- ContentPlan non-gating / comparison compatibility ---------------------------------------


def test_content_plan_is_valid_with_zero_and_with_existing_variants(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    plain = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "zero variants"}).json()["id"]
    pinned = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "has variants"}).json()["id"]
    version_id = _declare(fixtures, pinned).json()["id"]
    assert _declare_variant(fixtures, pinned, _vpayload(version_id)).status_code == 201
    plan_path = f"/api/v1/campaigns/{fixtures['campaign_id']}/plan"
    a = _post(fixtures, plan_path, {"summary": "plan without variants", "experiment_public_id": plain})
    b = _post(fixtures, plan_path, {"summary": "plan with variants", "experiment_public_id": pinned})
    assert a.status_code == 201, a.text
    assert b.status_code == 201, b.text
    assert set(a.json()["plan"]) == set(b.json()["plan"])
    assert not [k for k in b.json()["plan"] if "variant" in k.lower()]  # the plan carries no Variant linkage


@pytest.mark.parametrize(
    "comparison_type,factors,variant_count",
    [
        ("OBSERVATIONAL", [], 0),
        ("OBSERVATIONAL", [], 1),
        ("OBSERVATIONAL", ["Cohort"], 2),
        ("CONTROLLED", ["Format"], 0),
        ("CONTROLLED", ["Format"], 1),
        ("CONTROLLED", ["Format", "Timing"], 3),
    ],
)
def test_persistence_allows_any_variant_count_for_either_comparison_type(
    campaign_run_client: dict, comparison_type: str, factors: list[str], variant_count: int
) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    version = _declare(fixtures, experiment_id, _payload(comparison_type=comparison_type, controlled_factors=factors))
    assert version.status_code == 201
    for index in range(variant_count):
        response = _declare_variant(fixtures, experiment_id, _vpayload(version.json()["id"], label=f"Cond {index}"))
        assert response.status_code == 201, response.text
    assert _list(fixtures, experiment_id).json()["total"] == variant_count
