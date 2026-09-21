"""Structural database backstops for ExecutionStartAttestation (frozen
Governed Execution Start Design Freeze): every test here BYPASSES the service
and inserts directly, proving the database itself — not application code —
rejects the violation. All marked `postgres`.

Honest limits, documented rather than hidden: ``started_at >=
authorization.created_at`` and ``started_at <= now + 5 min`` are SERVICE-level
invariants only (cross-row / clock-relative — no CHECK can express them,
EXPROV-DISC-OBS-6), and "the Authorization is ACTIVE" is enforced by the lock
protocol, not by a constraint (cross-row state).
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.audit.models import ActorType, AuditEvent
from app.core.ids import generate_public_id
from app.strategy.execution_authorization_service import ExperimentExecutionAuthorizationService
from app.strategy.models import ExecutionStartAttestation
from tests.strategytest import build_current_experiment
from tests.test_execution_authorization_domain import _authorize, _ready

pytestmark = pytest.mark.postgres


def _authorized(session, *, suffix: str = "", **kwargs):
    """``suffix`` keeps the workspace-scoped idempotency keys of the upstream domains distinct when a
    second Experiment is built inside the SAME workspace."""
    campaign, _s, _h, experiment, actor = build_current_experiment(session, **kwargs)
    _ready(session, campaign, experiment, actor, d_key=f"d-1{suffix}", v_key=f"v-1{suffix}", c_key=f"c-1{suffix}")
    authorization, _snap, _created = _authorize(session, campaign, experiment, actor, key=f"a-1{suffix}")
    return campaign, experiment, actor, authorization


def _row(authorization, **overrides) -> ExecutionStartAttestation:
    values = dict(
        public_id=generate_public_id("EXS"), workspace_id=authorization.workspace_id,
        authorization_id=authorization.id, started_at=authorization.created_at + dt.timedelta(seconds=1),
        client_request_id=uuid.uuid4().hex,
    )
    values.update(overrides)
    return ExecutionStartAttestation(**values)


def _violation(session, row) -> str:
    with pytest.raises(IntegrityError) as excinfo:
        with session.begin_nested():
            session.add(row)
            session.flush()
    return excinfo.value.orig.diag.constraint_name


def test_a_second_start_for_the_same_authorization_is_rejected_by_the_database(db_session) -> None:
    _c, _e, _a, authorization = _authorized(db_session)
    db_session.add(_row(authorization))
    db_session.flush()
    assert _violation(db_session, _row(authorization)) == "uq_execution_start_attestations_authorization_id"


def test_duplicate_workspace_client_request_id_is_rejected_by_the_database(db_session) -> None:
    _c, _e, _a, first = _authorized(db_session)
    _c2, _e2, _a2, second = _authorized(
        db_session, suffix="-two", campaign_name="Same Workspace Two", within_workspace_id=first.workspace_id
    )
    db_session.add(_row(first, client_request_id="shared"))
    db_session.flush()
    assert _violation(db_session, _row(second, client_request_id="shared")) == (
        "uq_execution_start_attestations_workspace_client_request_id"
    )


def test_the_composite_fk_rejects_a_start_whose_workspace_differs_from_its_authorization(db_session) -> None:
    _c, _e, _a, authorization = _authorized(db_session)
    _c2, _e2, _a2, foreign = _authorized(db_session, campaign_name="Tenant Two")
    assert foreign.workspace_id != authorization.workspace_id
    # A real Authorization id paired with ANOTHER real workspace: only the composite FK can reject it.
    assert _violation(db_session, _row(authorization, workspace_id=foreign.workspace_id)) == (
        "fk_execution_start_attestations_authorization_workspace"
    )


def test_a_nonexistent_authorization_is_rejected(db_session) -> None:
    _c, _e, _a, authorization = _authorized(db_session)
    assert _violation(db_session, _row(authorization, authorization_id=uuid.uuid4())) == (
        "fk_execution_start_attestations_authorization_workspace"
    )


def test_started_at_is_required_and_has_no_server_default(db_session) -> None:
    _c, _e, _a, authorization = _authorized(db_session)
    columns = {c["name"]: c for c in inspect(db_session.get_bind()).get_columns("execution_start_attestations")}
    assert columns["started_at"]["nullable"] is False and columns["started_at"]["default"] is None
    assert columns["created_at"]["nullable"] is False and "now()" in str(columns["created_at"]["default"])
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "insert into execution_start_attestations (id, public_id, workspace_id, authorization_id, client_request_id) "
                    "values (:id, :public_id, :workspace_id, :authorization_id, :key)"
                ),
                {
                    "id": uuid.uuid4(), "public_id": generate_public_id("EXS"), "workspace_id": authorization.workspace_id,
                    "authorization_id": authorization.id, "key": "no-started-at",
                },
            )


def test_a_duplicate_public_id_is_rejected(db_session) -> None:
    _c, _e, _a, first = _authorized(db_session)
    _c2, _e2, _a2, second = _authorized(db_session, campaign_name="Tenant Two")
    db_session.add(_row(first, public_id="EXS-DUPLICATE01"))
    db_session.flush()
    assert _violation(db_session, _row(second, public_id="EXS-DUPLICATE01")) == "ix_execution_start_attestations_public_id"


def test_the_audit_fk_rejects_a_nonexistent_start(db_session) -> None:
    campaign, experiment, actor, authorization = _authorized(db_session)
    event = AuditEvent(
        workspace_id=campaign.workspace_id, event_type="strategy.execution_start.attested", actor_type=ActorType.USER,
        actor_user_id=actor.id, execution_start_attestation_id=uuid.uuid4(),
    )
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(event)
            db_session.flush()


def test_the_table_has_exactly_the_frozen_columns_and_no_banned_column(db_session) -> None:
    columns = {c["name"] for c in inspect(db_session.get_bind()).get_columns("execution_start_attestations")}
    assert columns == {"id", "public_id", "workspace_id", "authorization_id", "started_at", "created_at", "client_request_id"}
    assert not {
        "note", "external_reference", "created_by", "experiment_id", "definition_version_id", "contract_version_id",
        "strategy_id", "variant_id", "status", "active", "ended_at", "unit_reference", "cohort", "assignment",
        "tracking_valid", "result", "winner", "validity", "causality", "updated_at",
    } & columns


def test_the_constraint_set_is_exactly_the_frozen_one_with_no_check_and_no_speculative_key(db_session) -> None:
    rows = db_session.execute(
        text(
            "select conname, contype from pg_constraint where conrelid = to_regclass('execution_start_attestations') "
            "order by conname"
        )
    ).all()
    assert {(name, kind) for name, kind in rows} == {
        ("pk_execution_start_attestations", "p"),
        ("uq_execution_start_attestations_authorization_id", "u"),
        ("uq_execution_start_attestations_workspace_client_request_id", "u"),
        # Experiment Evidence Binding: the one candidate key added later, and NOT speculative — the claim table's
        # composite FK1 (start_id, authorization_id, workspace_id) is its concrete inbound reference.
        ("uq_execution_start_attestations_id_authorization_workspace", "u"),
        ("fk_execution_start_attestations_authorization_workspace", "f"),
        ("fk_execution_start_attestations_workspace_id_workspaces", "f"),
    }
    definition = db_session.scalar(
        text(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conname = 'fk_execution_start_attestations_authorization_workspace'"
        )
    )
    assert "(authorization_id, workspace_id)" in definition
    assert "execution_authorizations(id, workspace_id)" in definition and "CASCADE" not in definition


def test_every_postgresql_identifier_of_this_domain_is_within_the_63_character_limit(db_session) -> None:
    names = set(
        db_session.scalars(
            text(
                "select conname from pg_constraint where conrelid = to_regclass('execution_start_attestations') "
                "or conname like 'fk_audit_events_execution_start%'"
            )
        )
    )
    names |= set(
        db_session.scalars(
            text(
                "select indexname from pg_indexes where tablename = 'execution_start_attestations' "
                "or indexname like '%execution_start_attestation%'"
            )
        )
    )
    names |= {"execution_start_attestations", "execution_start_attestation_id"}
    assert "fk_audit_events_execution_start_attestation_id" in names
    assert all(len(name) <= 63 for name in names), [n for n in names if len(n) > 63]
    # The convention-derived audit FK name would have exceeded the limit — the explicit name is load-bearing.
    assert len("fk_audit_events_execution_start_attestation_id_execution_start_attestations") > 63
