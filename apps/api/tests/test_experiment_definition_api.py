"""API contract, validation, versioning, idempotency, eligibility, audit,
tenancy, non-effects and experimental-claim-firewall tests for Governed
Experiment Definition (MVP-37, implementing the frozen MVP-37A/-37B
contract). All marked `postgres`.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.audit.models import ActorType, AuditEvent
from app.persistence.base import Base
from app.persistence.session import get_engine
from app.strategy.models import Experiment, ExperimentDefinitionVersion, Hypothesis
from app.users.models import User
from app.workspaces.models import MembershipRole, Membership, MembershipStatus
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.settingstest import add_member_to_workspace, login_as
from tests.test_experiment_api import (
    _build_base_strategy,
    _build_hypothesis,
    _experiments_path,
    _post,
    _record_approved_approval,
)

pytestmark = pytest.mark.postgres

FORBIDDEN_FIELDS = [
    "required_signal", "metric", "evidence_type", "success_criterion", "directionality", "measurement_window",
    "minimum_evidence", "source_requirement", "stopping_rule", "decision_rule", "analysis_method",
    "tracking_requirement", "allocation", "randomization", "variant", "contamination", "result", "winner",
]
PROSE_FIELDS = ["comparison_question", "comparison_basis", "scope", "learning_intent", "non_conclusion_boundary"]


def _payload(**overrides: object) -> dict:
    body: dict = {
        "base_version": 0,
        "client_request_id": uuid.uuid4().hex,
        "comparison_question": "Does a question hook change the share of viewers who watch to the end?",
        "comparison_type": "OBSERVATIONAL",
        "changed_factor": "Opening hook",
        "controlled_factors": [],
        "comparison_basis": "The current opening hook used in the latest published piece.",
        "scope": "Instagram Reels, existing followers, one calendar month.",
        "learning_intent": "Decide which hook style to use in the next content cycle.",
        "non_conclusion_boundary": "This does not establish that the hook caused any difference.",
    }
    body.update(overrides)
    return body


def _defs_path(fixtures: dict, experiment_id: str, campaign_id: str | None = None) -> str:
    return (
        f"/api/v1/campaigns/{campaign_id or fixtures['campaign_id']}/experiments/{experiment_id}/definition-versions"
    )


def _declare(fixtures: dict, experiment_id: str, body: dict | None = None):
    return _post(fixtures, _defs_path(fixtures, experiment_id), body if body is not None else _payload())


def _history(fixtures: dict, experiment_id: str):
    return fixtures["client"].get(_defs_path(fixtures, experiment_id))


def _experiment(fixtures: dict) -> tuple[str, str, str]:
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    created = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "Compare two hooks."})
    assert created.status_code == 201, created.text
    return strategy_id, hypothesis_id, created.json()["id"]


def _supersede_strategy(fixtures: dict, strategy_id: str) -> str:
    approval_id = _record_approved_approval(fixtures)
    revised = _post(
        fixtures,
        f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy/{strategy_id}/revision",
        {"strategic_approval_id": approval_id, "summary": "x", "positioning_statement": "y"},
    )
    assert revised.status_code == 201, revised.text
    return revised.json()["strategy"]["id"]


def _table_counts() -> dict[str, int]:
    with get_engine().connect() as connection:
        return {
            table.name: connection.execute(select(func.count()).select_from(table)).scalar_one()
            for table in Base.metadata.sorted_tables
        }


def _delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {name: after[name] - before[name] for name in after if after[name] != before[name]}


def _definition_audit(experiment_public_id: str) -> list[dict]:
    with OrmSession(get_engine()) as session:
        experiment = session.execute(select(Experiment).where(Experiment.public_id == experiment_public_id)).scalar_one()
        rows = session.execute(
            select(AuditEvent)
            .where(AuditEvent.experiment_id == experiment.id, AuditEvent.event_type.like("strategy.experiment_definition.%"))
            .order_by(AuditEvent.created_at, AuditEvent.id)
        ).scalars().all()
        return [
            {
                "event_type": r.event_type, "previous_state": r.previous_state, "new_state": r.new_state,
                "actor_type": r.actor_type, "actor_user_id": r.actor_user_id, "workspace_id": r.workspace_id,
                "campaign_id": r.campaign_id, "strategy_id": r.strategy_id, "hypothesis_id": r.hypothesis_id,
                "experiment_id": r.experiment_id, "version_id": r.experiment_definition_version_id,
                "request_id": r.request_id,
            }
            for r in rows
        ]


# --- first declaration / revision / tip / history ---------------------------------


def test_first_declaration_creates_version_one(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    body = _payload()
    response = _declare(fixtures, experiment_id, body)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["id"].startswith("EXD-") and len(data["id"]) == 16
    assert data["experiment_id"] == experiment_id
    assert data["version"] == 1
    assert data["comparison_type"] == "OBSERVATIONAL"
    assert data["controlled_factors"] == []
    assert data["non_conclusion_codes"] == [
        "NO_ATTRIBUTION_ESTABLISHED", "NO_STATISTICAL_VALIDITY_ESTABLISHED", "NO_RESULT_OR_WINNER",
        "CANNOT_ESTABLISH_CAUSALITY",
    ]
    assert data["created_at"]
    assert set(data) == {
        "id", "experiment_id", "version", "comparison_question", "comparison_type", "changed_factor",
        "controlled_factors", "comparison_basis", "scope", "learning_intent", "non_conclusion_boundary",
        "non_conclusion_codes", "created_at",
    }


def test_revision_appends_version_two_and_keeps_version_one_immutable(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    first = _declare(fixtures, experiment_id)
    assert first.status_code == 201
    with OrmSession(get_engine()) as session:
        row = session.execute(
            select(ExperimentDefinitionVersion).where(ExperimentDefinitionVersion.public_id == first.json()["id"])
        ).scalar_one()
        snapshot = (row.id, row.created_at, row.comparison_question, row.changed_factor, list(row.controlled_factors))

    revision = _declare(
        fixtures,
        experiment_id,
        _payload(
            base_version=1,
            comparison_type="CONTROLLED",
            comparison_question="Does a stronger CTA change click-through?",
            changed_factor="Call to action",
            controlled_factors=["Posting time", "Format"],
        ),
    )
    assert revision.status_code == 201, revision.text
    assert revision.json()["version"] == 2
    assert revision.json()["id"] != first.json()["id"]
    assert revision.json()["non_conclusion_codes"][-2:] == [
        "CAUSALITY_NOT_ESTABLISHED_BY_DEFINITION", "CONTROLLED_VALIDITY_NOT_ESTABLISHED",
    ]

    with OrmSession(get_engine()) as session:
        row = session.execute(
            select(ExperimentDefinitionVersion).where(ExperimentDefinitionVersion.public_id == first.json()["id"])
        ).scalar_one()
        assert (row.id, row.created_at, row.comparison_question, row.changed_factor, list(row.controlled_factors)) == snapshot

    history = _history(fixtures, experiment_id).json()
    assert [v["version"] for v in history["versions"]] == [1, 2]
    assert history["current_version"] == 2
    assert history["comparison_label"] == "DECLARED_CONTROLLED_INTENT"
    assert history["experiment_id"] == experiment_id
    assert history["versions"][0]["comparison_type"] == "OBSERVATIONAL"


def test_tip_is_embedded_in_get_strategy_and_experiment_create_response(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    created = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "Compare two hooks."})
    assert created.json()["definition"] is None
    assert created.json()["comparison_label"] == "NO_COMPARISON_DECLARED"
    assert created.json()["status"] == "RECORDED"
    experiment_id = created.json()["id"]

    def experiments() -> list[dict]:
        return fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").json()["experiments"]

    assert experiments()[0]["definition"] is None
    assert experiments()[0]["comparison_label"] == "NO_COMPARISON_DECLARED"

    assert _declare(fixtures, experiment_id).status_code == 201
    tip = experiments()[0]
    assert tip["comparison_label"] == "DECLARED_OBSERVATIONAL_INTENT"
    assert tip["definition"]["version"] == 1

    assert _declare(fixtures, experiment_id, _payload(base_version=1, changed_factor="CTA wording")).status_code == 201
    tip = experiments()[0]
    assert tip["definition"]["version"] == 2 and tip["definition"]["changed_factor"] == "CTA wording"
    # Additive only: every pre-existing Experiment field is unchanged.
    assert {"id", "hypothesis_id", "description", "status", "created_at"} <= set(tip)


def test_history_for_definition_less_experiment_is_empty(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    body = _history(fixtures, experiment_id).json()
    assert body == {
        "experiment_id": experiment_id,
        "comparison_label": "NO_COMPARISON_DECLARED",
        "current_version": None,
        "versions": [],
    }


# --- authority / tenancy ----------------------------------------------------------


@pytest.mark.parametrize("role", [MembershipRole.ADMIN, MembershipRole.MEMBER])
def test_admin_and_member_can_declare_and_read(campaign_run_client: dict, role: MembershipRole) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=role)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}
    assert _declare(member_fixtures, experiment_id).status_code == 201
    assert _history(member_fixtures, experiment_id).status_code == 200


def test_revoked_membership_is_forbidden(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    me = fixtures["client"].get("/api/v1/users/me").json()
    with OrmSession(get_engine()) as session:
        user = session.execute(select(User).where(User.public_id == me["id"])).scalar_one()
        membership = session.execute(select(Membership).where(Membership.user_id == user.id)).scalar_one()
        membership.status = MembershipStatus.REVOKED
        session.commit()
    write = _declare(fixtures, experiment_id)
    assert write.status_code == 403 and write.json()["error"]["code"] == "FORBIDDEN"
    read = _history(fixtures, experiment_id)
    assert read.status_code == 403 and read.json()["error"]["code"] == "FORBIDDEN"


def test_unauthenticated_and_csrf(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    anonymous = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    assert anonymous.post(_defs_path(fixtures, experiment_id), json=_payload()).status_code == 401
    assert anonymous.get(_defs_path(fixtures, experiment_id)).status_code == 401
    no_csrf = fixtures["client"].post(_defs_path(fixtures, experiment_id), json=_payload())
    assert no_csrf.status_code == 403 and no_csrf.json()["error"]["code"] == "CSRF_INVALID"
    assert _declare(fixtures, experiment_id).status_code == 201  # CSRF failure wrote nothing


def test_no_patch_put_or_delete_surface(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    path = _defs_path(fixtures, experiment_id)
    for method in ("patch", "put", "delete"):
        assert getattr(fixtures["client"], method)(path, headers=headers).status_code == 405


def test_unknown_experiment_is_a_non_leaky_403(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    before = _table_counts()
    write = _declare(fixtures, "EXP-TOTALLYFAKE0")
    assert write.status_code == 403 and write.json()["error"]["code"] == "FORBIDDEN"
    read = _history(fixtures, "EXP-TOTALLYFAKE0")
    assert read.status_code == 403 and read.json()["error"]["code"] == "FORBIDDEN"
    assert _delta(before, _table_counts()) == {}


def test_cross_workspace_and_cross_campaign_are_forbidden(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")
    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    campaign_a = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A"), headers={"X-CSRF-Token": csrf_a}).json()["campaign"]["id"]
    campaign_a2 = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A2"), headers={"X-CSRF-Token": csrf_a}).json()["campaign"]["id"]
    fixtures_a = {"client": client_a, "csrf_token": csrf_a, "campaign_id": campaign_a}
    _, _, experiment_a = _experiment(fixtures_a)

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    campaign_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()["campaign"]["id"]
    fixtures_b = {"client": client_b, "csrf_token": csrf_b, "campaign_id": campaign_b}

    before = _table_counts()
    # cross-workspace: B names A's Experiment through B's own campaign, and through A's campaign
    for campaign_id in (campaign_b, campaign_a):
        write = _post(fixtures_b, _defs_path(fixtures_b, experiment_a, campaign_id), _payload())
        assert write.status_code == 403 and write.json()["error"]["code"] == "FORBIDDEN"
        read = client_b.get(_defs_path(fixtures_b, experiment_a, campaign_id))
        assert read.status_code == 403 and read.json()["error"]["code"] == "FORBIDDEN"
    # cross-campaign, same workspace: A's Experiment named through A's OTHER campaign
    write = _post(fixtures_a, _defs_path(fixtures_a, experiment_a, campaign_a2), _payload())
    assert write.status_code == 403 and write.json()["error"]["code"] == "FORBIDDEN"
    assert client_a.get(_defs_path(fixtures_a, experiment_a, campaign_a2)).status_code == 403
    assert _delta(before, _table_counts()) == {}


# --- validation -------------------------------------------------------------------


@pytest.mark.parametrize("value", ["RANDOMIZED", "NON_RANDOMIZED", "SEQUENTIAL", "VALID", "CAUSAL", "controlled", "", None, 3])
def test_comparison_type_vocabulary_is_exactly_two_values(campaign_run_client: dict, value: object) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    response = _declare(fixtures, experiment_id, _payload(comparison_type=value))
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_controlled_requires_at_least_one_factor_and_observational_may_have_none(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    assert _declare(fixtures, experiment_id, _payload(comparison_type="CONTROLLED", controlled_factors=[])).status_code == 422
    ok = _declare(fixtures, experiment_id, _payload(comparison_type="OBSERVATIONAL", controlled_factors=["Format", "Time"]))
    assert ok.status_code == 201


@pytest.mark.parametrize(
    "factors",
    [
        ["Format", "format"],
        ["Post  time", "post time"],
        ["  Format  ", "FORMAT"],
        ["a"] * 2,
    ],
)
def test_duplicate_controlled_factors_are_rejected(campaign_run_client: dict, factors: list[str]) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    response = _declare(fixtures, experiment_id, _payload(comparison_type="CONTROLLED", controlled_factors=factors))
    assert response.status_code == 422


@pytest.mark.parametrize("changed,factors", [("Format", ["format"]), ("Opening  Hook", ["x", "opening hook"]), ("  CTA ", ["cta"])])
def test_changed_factor_overlapping_controlled_factors_is_rejected(campaign_run_client: dict, changed: str, factors: list[str]) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    response = _declare(fixtures, experiment_id, _payload(comparison_type="CONTROLLED", changed_factor=changed, controlled_factors=factors))
    assert response.status_code == 422


def test_length_and_count_bounds(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    for field in PROSE_FIELDS:
        assert _declare(fixtures, experiment_id, _payload(**{field: "x" * 1001})).status_code == 422, field
    assert _declare(fixtures, experiment_id, _payload(changed_factor="x" * 201)).status_code == 422
    assert _declare(fixtures, experiment_id, _payload(controlled_factors=["x" * 201])).status_code == 422
    assert _declare(fixtures, experiment_id, _payload(controlled_factors=[f"f{i}" for i in range(21)])).status_code == 422
    assert _declare(fixtures, experiment_id, _payload(client_request_id="k" * 101)).status_code == 422
    for field in [*PROSE_FIELDS, "changed_factor"]:
        assert _declare(fixtures, experiment_id, _payload(**{field: "   "})).status_code == 422, field
        assert _declare(fixtures, experiment_id, _payload(**{field: ""})).status_code == 422, field
        assert _declare(fixtures, experiment_id, _payload(**{field: None})).status_code == 422, field
    assert _declare(fixtures, experiment_id, _payload(controlled_factors=[""])).status_code == 422
    assert _declare(fixtures, experiment_id, _payload(controlled_factors=["   "])).status_code == 422
    assert _declare(fixtures, experiment_id, _payload(controlled_factors=[5])).status_code == 422
    assert _declare(fixtures, experiment_id, _payload(controlled_factors="Format")).status_code == 422
    assert _declare(fixtures, experiment_id, _payload(client_request_id="")).status_code == 422
    # Exact upper bounds succeed (after trimming; padding is not counted).
    ok = _payload(
        comparison_question=" " + "q" * 1000 + " ",
        changed_factor="c" * 200,
        controlled_factors=[f"factor-{i:02d}" for i in range(19)] + ["z" * 200],
        client_request_id="k" * 100,
    )
    response = _declare(fixtures, experiment_id, ok)
    assert response.status_code == 201, response.text
    assert response.json()["comparison_question"] == "q" * 1000


@pytest.mark.parametrize("field", [*PROSE_FIELDS, "changed_factor", "client_request_id"])
def test_nul_is_rejected_in_every_string_field(campaign_run_client: dict, field: str) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    response = _declare(fixtures, experiment_id, _payload(**{field: "bad\x00value"}))
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_nul_is_rejected_in_a_controlled_factor(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    assert _declare(fixtures, experiment_id, _payload(controlled_factors=["a\x00b"])).status_code == 422


@pytest.mark.parametrize("value", ["a\nb", "a\rb"])
def test_factors_must_be_single_line_but_prose_may_contain_newlines(campaign_run_client: dict, value: str) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    assert _declare(fixtures, experiment_id, _payload(changed_factor=value)).status_code == 422
    assert _declare(fixtures, experiment_id, _payload(controlled_factors=[value])).status_code == 422
    assert _declare(fixtures, experiment_id, _payload(scope="line one\nline two")).status_code == 201


def test_normalization_trims_outer_whitespace_only_and_preserves_content(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    response = _declare(
        fixtures,
        experiment_id,
        _payload(
            comparison_type="CONTROLLED",
            comparison_question="  Line one\n   Line   two  \n",
            changed_factor="  Opening  Hook  ",
            controlled_factors=["  Zeta  ", "alpha", "Beta  gamma"],
            scope="\tScope Text\t",
        ),
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["comparison_question"] == "Line one\n   Line   two"
    assert data["changed_factor"] == "Opening  Hook"
    assert data["controlled_factors"] == ["Zeta", "alpha", "Beta  gamma"]  # order + case + inner spacing preserved
    assert data["scope"] == "Scope Text"


@pytest.mark.parametrize("field", FORBIDDEN_FIELDS)
def test_forbidden_fields_are_rejected(campaign_run_client: dict, field: str) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    response = _declare(fixtures, experiment_id, _payload(**{field: "anything"}))
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("value", ["0", True, -1, 1.5, None, 2**31])
def test_base_version_must_be_a_non_negative_strict_integer(campaign_run_client: dict, value: object) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    assert _declare(fixtures, experiment_id, _payload(base_version=value)).status_code == 422


def test_missing_required_fields_are_rejected(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    for field in _payload():
        body = _payload()
        del body[field]
        assert _declare(fixtures, experiment_id, body).status_code == 422, field


# --- stale base / unchanged --------------------------------------------------------


def test_stale_base_version_is_a_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    before = _table_counts()
    stale = _declare(fixtures, experiment_id, _payload(base_version=1))
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "EXPERIMENT_DEFINITION_BASE_STALE"
    assert _delta(before, _table_counts()) == {}
    assert _declare(fixtures, experiment_id).status_code == 201
    for base in (0, 2, 5):
        response = _declare(fixtures, experiment_id, _payload(base_version=base, changed_factor="Other"))
        assert response.status_code == 409 and response.json()["error"]["code"] == "EXPERIMENT_DEFINITION_BASE_STALE"


def test_revision_identical_to_the_tip_is_rejected_but_order_change_is_a_new_version(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    base = {"comparison_type": "CONTROLLED", "controlled_factors": ["A", "B"]}
    assert _declare(fixtures, experiment_id, _payload(**base)).status_code == 201
    unchanged = _declare(fixtures, experiment_id, _payload(base_version=1, **base))
    assert unchanged.status_code == 409 and unchanged.json()["error"]["code"] == "EXPERIMENT_DEFINITION_UNCHANGED"
    # Whitespace-only differences normalize to the same content -> still unchanged.
    padded = _declare(fixtures, experiment_id, _payload(base_version=1, comparison_type="CONTROLLED", controlled_factors=[" A ", "B"]))
    assert padded.status_code == 409 and padded.json()["error"]["code"] == "EXPERIMENT_DEFINITION_UNCHANGED"
    reordered = _declare(fixtures, experiment_id, _payload(base_version=1, comparison_type="CONTROLLED", controlled_factors=["B", "A"]))
    assert reordered.status_code == 201 and reordered.json()["version"] == 2
    # Case-sensitive: a case-only change is a real change.
    cased = _declare(fixtures, experiment_id, _payload(base_version=2, comparison_type="CONTROLLED", controlled_factors=["b", "A"]))
    assert cased.status_code == 201


def test_stale_base_takes_precedence_over_unchanged(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    assert _declare(fixtures, experiment_id, _payload(client_request_id="one")).status_code == 201
    assert _declare(fixtures, experiment_id, _payload(base_version=1, changed_factor="Second")).status_code == 201
    # Identical to v1 but base 1 is stale (tip is 2) -> BASE_STALE, not UNCHANGED.
    response = _declare(fixtures, experiment_id, _payload(base_version=1))
    assert response.status_code == 409 and response.json()["error"]["code"] == "EXPERIMENT_DEFINITION_BASE_STALE"


# --- idempotency / replay ----------------------------------------------------------


def test_same_key_same_request_is_a_200_replay_with_no_new_rows(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    body = _payload()
    first = _declare(fixtures, experiment_id, body)
    assert first.status_code == 201
    before = _table_counts()
    replay = _declare(fixtures, experiment_id, body)
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert _delta(before, _table_counts()) == {}
    assert len(_definition_audit(experiment_id)) == 1


def test_replay_of_an_older_version_after_a_newer_one_returns_the_stored_row(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    body = _payload()
    first = _declare(fixtures, experiment_id, body)
    assert _declare(fixtures, experiment_id, _payload(base_version=1, changed_factor="Second")).status_code == 201
    before = _table_counts()
    replay = _declare(fixtures, experiment_id, body)
    assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"] and replay.json()["version"] == 1
    assert _delta(before, _table_counts()) == {}


def test_same_key_with_a_different_material_request_is_an_idempotency_conflict(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    body = _payload()
    assert _declare(fixtures, experiment_id, body).status_code == 201
    before = _table_counts()
    variants = [
        {**body, "scope": "A different scope."},
        {**body, "comparison_type": "CONTROLLED", "controlled_factors": ["X"]},
        {**body, "controlled_factors": ["X"]},
        {**body, "base_version": 1},
    ]
    for variant in variants:
        response = _declare(fixtures, experiment_id, variant)
        assert response.status_code == 409 and response.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT", variant
    assert _delta(before, _table_counts()) == {}


def test_key_reused_for_another_experiment_is_a_conflict_and_leaks_nothing(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    first_experiment = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "one"}).json()["id"]
    second_experiment = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "two"}).json()["id"]
    body = _payload()
    assert _declare(fixtures, first_experiment, body).status_code == 201
    response = _declare(fixtures, second_experiment, body)
    assert response.status_code == 409 and response.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert first_experiment not in response.text
    assert _history(fixtures, second_experiment).json()["versions"] == []


def test_the_key_is_workspace_scoped_not_global(auth_client: TestClient) -> None:
    key = uuid.uuid4().hex
    results = []
    for name in ("Tenant One", "Tenant Two"):
        client = TestClient(auth_client.app, raise_server_exceptions=False)
        register_and_get_csrf(client, display_name=name)
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        campaign_id = client.post("/api/v1/campaigns", json=campaign_payload(name=name), headers={"X-CSRF-Token": csrf}).json()["campaign"]["id"]
        fixtures = {"client": client, "csrf_token": csrf, "campaign_id": campaign_id}
        _, _, experiment_id = _experiment(fixtures)
        results.append(_declare(fixtures, experiment_id, _payload(client_request_id=key)).status_code)
    assert results == [201, 201]


def test_different_key_after_the_first_write_is_a_stale_base_not_a_replay(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    body = _payload()
    assert _declare(fixtures, experiment_id, body).status_code == 201
    retry = _declare(fixtures, experiment_id, {**body, "client_request_id": uuid.uuid4().hex})
    assert retry.status_code == 409 and retry.json()["error"]["code"] == "EXPERIMENT_DEFINITION_BASE_STALE"


# --- current-Strategy eligibility ---------------------------------------------------


def test_superseded_strategy_rejects_new_writes_but_allows_reads_and_matching_replay(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id, _, experiment_id = _experiment(fixtures)
    body = _payload()
    first = _declare(fixtures, experiment_id, body)
    assert first.status_code == 201

    _supersede_strategy(fixtures, strategy_id)

    before = _table_counts()
    new_revision = _declare(fixtures, experiment_id, _payload(base_version=1, changed_factor="Later"))
    assert new_revision.status_code == 409 and new_revision.json()["error"]["code"] == "EXPERIMENT_DEFINITION_STRATEGY_STALE"
    assert _delta(before, _table_counts()) == {}

    # History of a historical-Strategy Experiment stays readable.
    history = _history(fixtures, experiment_id)
    assert history.status_code == 200 and history.json()["current_version"] == 1
    # A matching replay of the already-committed write is still a 200.
    replay = _declare(fixtures, experiment_id, body)
    assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"]
    assert _delta(before, _table_counts()) == {}
    # GET /strategy no longer lists it (MVP37A-OBS-2) — the history route is the read path.
    listed = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").json()["experiments"]
    assert experiment_id not in [e["id"] for e in listed]


def test_superseded_strategy_rejects_a_new_first_declaration_and_definition_less_history_is_readable(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id, _, experiment_id = _experiment(fixtures)
    _supersede_strategy(fixtures, strategy_id)
    stale = _declare(fixtures, experiment_id)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "EXPERIMENT_DEFINITION_STRATEGY_STALE"
    history = _history(fixtures, experiment_id)
    assert history.status_code == 200
    assert history.json() == {
        "experiment_id": experiment_id, "comparison_label": "NO_COMPARISON_DECLARED",
        "current_version": None, "versions": [],
    }


def test_hypothesis_status_does_not_gate_definition_writes(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, hypothesis_id, experiment_id = _experiment(fixtures)
    with OrmSession(get_engine()) as session:
        from app.strategy.models import HypothesisStatus

        hypothesis = session.execute(select(Hypothesis).where(Hypothesis.public_id == hypothesis_id)).scalar_one()
        hypothesis.status = HypothesisStatus.REFUTED
        session.commit()
    assert _declare(fixtures, experiment_id).status_code == 201


# --- ContentPlan non-gating -----------------------------------------------------------


def test_content_plan_is_not_gated_by_a_definition(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    without = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "no definition"}).json()["id"]
    with_definition = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "defined"}).json()["id"]
    assert _declare(fixtures, with_definition).status_code == 201

    plan_path = f"/api/v1/campaigns/{fixtures['campaign_id']}/plan"
    plan_a = _post(fixtures, plan_path, {"summary": "plans for the undefined experiment", "experiment_public_id": without})
    assert plan_a.status_code == 201, plan_a.text
    assert plan_a.json()["plan"]["experiment_id"] == without
    plan_b = _post(fixtures, plan_path, {"summary": "plans for the defined experiment", "experiment_public_id": with_definition})
    assert plan_b.status_code == 201, plan_b.text
    assert plan_b.json()["plan"]["experiment_id"] == with_definition
    assert set(plan_a.json()["plan"]) == set(plan_b.json()["plan"])


def test_content_plan_for_a_superseded_strategy_experiment_is_unchanged(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id, _, experiment_id = _experiment(fixtures)
    _supersede_strategy(fixtures, strategy_id)
    plan = _post(
        fixtures, f"/api/v1/campaigns/{fixtures['campaign_id']}/plan",
        {"summary": "historical experiment plan", "experiment_public_id": experiment_id},
    )
    assert plan.status_code == 201, plan.text


# --- audit ---------------------------------------------------------------------------


def test_exactly_one_user_audit_event_per_committed_version(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, hypothesis_id, experiment_id = _experiment(fixtures)
    first_body = _payload()
    first = _declare(fixtures, experiment_id, first_body)
    _declare(fixtures, experiment_id, first_body)  # replay: no event
    _declare(fixtures, experiment_id, _payload(base_version=7))  # stale: no event
    _declare(fixtures, experiment_id, _payload(base_version=1, changed_factor=""))  # invalid: no event
    second = _declare(fixtures, experiment_id, _payload(base_version=1, changed_factor="Second factor"))
    _declare(fixtures, experiment_id, _payload(base_version=2, changed_factor="Second factor"))  # unchanged: no event

    events = _definition_audit(experiment_id)
    assert [e["event_type"] for e in events] == [
        "strategy.experiment_definition.declared", "strategy.experiment_definition.revised",
    ]
    assert [(e["previous_state"], e["new_state"]) for e in events] == [(None, "v1"), ("v1", "v2")]
    assert all(e["actor_type"] is ActorType.USER and e["actor_user_id"] is not None for e in events)
    assert all(e["request_id"] for e in events)
    assert len({e["version_id"] for e in events}) == 2
    with OrmSession(get_engine()) as session:
        versions = {
            v.public_id: v.id for v in session.execute(select(ExperimentDefinitionVersion)).scalars()
        }
        assert [e["version_id"] for e in events] == [versions[first.json()["id"]], versions[second.json()["id"]]]
        experiment = session.execute(select(Experiment).where(Experiment.public_id == experiment_id)).scalar_one()
        hypothesis = session.execute(select(Hypothesis).where(Hypothesis.public_id == hypothesis_id)).scalar_one()
        assert all(
            e["experiment_id"] == experiment.id and e["hypothesis_id"] == hypothesis.id
            and e["strategy_id"] == hypothesis.strategy_id and e["workspace_id"] == experiment.workspace_id
            and e["campaign_id"] is not None
            for e in events
        )


# --- non-effects / claim firewall -------------------------------------------------------


def test_definition_writes_touch_only_the_definition_and_audit_tables(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, hypothesis_id, experiment_id = _experiment(fixtures)

    def semantic_state() -> tuple:
        with OrmSession(get_engine()) as session:
            experiment = session.execute(select(Experiment).where(Experiment.public_id == experiment_id)).scalar_one()
            hypothesis = session.execute(select(Hypothesis).where(Hypothesis.public_id == hypothesis_id)).scalar_one()
            return (
                experiment.status, experiment.description, experiment.updated_at,
                hypothesis.status, hypothesis.statement, hypothesis.updated_at,
            )

    state_before = semantic_state()
    before = _table_counts()
    assert _declare(fixtures, experiment_id).status_code == 201
    assert _delta(before, _table_counts()) == {"experiment_definition_versions": 1, "audit_events": 1}

    before = _table_counts()
    assert _declare(fixtures, experiment_id, _payload(base_version=1, changed_factor="Other factor")).status_code == 201
    assert _delta(before, _table_counts()) == {"experiment_definition_versions": 1, "audit_events": 1}

    before = _table_counts()
    body = _payload(base_version=2, changed_factor="Third factor")
    assert _declare(fixtures, experiment_id, body).status_code == 201
    assert _declare(fixtures, experiment_id, body).status_code == 200  # replay
    assert _declare(fixtures, experiment_id, {**body, "scope": "different"}).status_code == 409  # key conflict
    assert _declare(fixtures, experiment_id, _payload(base_version=0)).status_code == 409  # stale
    assert _declare(fixtures, experiment_id, _payload(base_version=3, changed_factor="")).status_code == 422
    assert _delta(before, _table_counts()) == {"experiment_definition_versions": 1, "audit_events": 1}

    assert semantic_state() == state_before  # Experiment and Hypothesis are untouched


def test_definition_makes_no_experimental_claim(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _, _, experiment_id = _experiment(fixtures)
    for comparison_type, factors, expected in (
        ("OBSERVATIONAL", [], "DECLARED_OBSERVATIONAL_INTENT"),
        ("CONTROLLED", ["Format"], "DECLARED_CONTROLLED_INTENT"),
    ):
        base = 0 if comparison_type == "OBSERVATIONAL" else 1
        response = _declare(
            fixtures, experiment_id, _payload(base_version=base, comparison_type=comparison_type, controlled_factors=factors)
        )
        assert response.status_code == 201
        tip = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").json()["experiments"][0]
        assert tip["comparison_label"] == expected
        assert tip["status"] == "RECORDED"
        for word in ("VALID", "CAUSAL", "WINNER", "RESULT"):
            assert word not in tip["comparison_label"]
        assert not any(word in code for code in tip["definition"]["non_conclusion_codes"] for word in ("VALID_EXPERIMENT", "WINNER_DECLARED"))
        assert {"NO_ATTRIBUTION_ESTABLISHED", "NO_STATISTICAL_VALIDITY_ESTABLISHED", "NO_RESULT_OR_WINNER"} <= set(
            tip["definition"]["non_conclusion_codes"]
        )
    hypotheses = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").json()["hypotheses"]
    assert all(h["status"] == "OPEN" for h in hypotheses)
    # No persisted validity / causal / result / execution field exists on the version row.
    columns = set(ExperimentDefinitionVersion.__table__.columns.keys())
    assert columns == {
        "id", "public_id", "workspace_id", "experiment_id", "version", "comparison_question", "comparison_type",
        "changed_factor", "controlled_factors", "comparison_basis", "scope", "learning_intent",
        "non_conclusion_boundary", "client_request_id", "created_at",
    }
    assert not [
        t for t in Base.metadata.tables if any(word in t for word in ("variant", "allocation", "randomiz", "contamination"))
    ]


def test_route_surface_adds_exactly_the_two_frozen_routes() -> None:
    from app.main import create_app

    spec = create_app().openapi()["paths"]
    pairs = {(method.upper(), path) for path, item in spec.items() for method in item if method in {"get", "post", "put", "patch", "delete"}}
    assert len(pairs) == 99  # 97 before MVP-37 + exactly the two frozen definition routes
    definition_pairs = {(m, p) for m, p in pairs if "definition" in p}
    route = "/api/v1/campaigns/{campaign_public_id}/experiments/{experiment_public_id}/definition-versions"
    assert definition_pairs == {("POST", route), ("GET", route)}
    for word in ("variant", "allocation", "randomiz", "execution", "contamination", "winner", "measurement-contract"):
        assert not [p for _, p in pairs if word in p], word
