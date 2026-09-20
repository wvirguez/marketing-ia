"""Structural database backstops for ExecutionAuthorization and
ExecutionAuthorizationVariant (MVP-40, frozen Execution Authorization
Discovery/Design Freeze): every test here BYPASSES the service and inserts
directly, proving the database itself — not application code — rejects the
violation. All marked `postgres`.

Honest limit, documented rather than hidden: OBSERVATIONAL/CONTROLLED
minimum Variant cardinality is a SERVICE-level invariant only, the same
class of gap as MVP39B-OBS-1/MVP38A-OBS-1.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.audit.models import ActorType, AuditEvent
from app.core.ids import generate_public_id
from app.strategy.definition_service import ExperimentDefinitionService
from app.strategy.measurement_contract_service import ExperimentMeasurementContractService
from app.strategy.models import ExecutionAuthorization, ExecutionAuthorizationVariant, ExperimentVariant
from app.strategy.repository import ExecutionAuthorizationRepository
from app.strategy.variant_service import ExperimentVariantService
from tests.strategytest import build_current_experiment
from tests.test_experiment_definition_domain import fields

pytestmark = pytest.mark.postgres


def _version(session, campaign, experiment, actor, *, base_version=0, key="v-1", **overrides):
    version, _created = ExperimentDefinitionService(session).write_version(
        campaign=campaign, experiment_public_id=experiment.public_id, base_version=base_version,
        client_request_id=key, fields=fields(**overrides), actor_user_id=actor.id,
    )
    return version


def _variant(session, campaign, experiment, actor, version, *, label="A", key="v-1"):
    variant, _pinned, _created = ExperimentVariantService(session).declare_variant(
        campaign=campaign, experiment_public_id=experiment.public_id, definition_version_public_id=version.public_id,
        label=label, condition_description="d", client_request_id=key, actor_user_id=actor.id,
    )
    return variant


def _contract(session, campaign, experiment, actor, version, *, key="c-1"):
    signal = {
        "name": "CTR", "description": "d", "expected_direction": None, "evidence_requirement": None,
        "tracking_required": False,
    }
    contract, _signals, _pinned, _created = ExperimentMeasurementContractService(session).declare_or_revise(
        campaign=campaign, experiment_public_id=experiment.public_id, base_version=0, client_request_id=key,
        definition_version_public_id=version.public_id, measurement_window_days=None, minimum_evidence=None,
        success_criterion=None, analysis_method_intent=None, stopping_rule=None, decision_rule_intent=None,
        signals=[signal], actor_user_id=actor.id,
    )
    return contract


def _authorization_row(*, definition_version, contract, **overrides) -> ExecutionAuthorization:
    values = dict(
        public_id=generate_public_id("EXA"), workspace_id=definition_version.workspace_id,
        experiment_id=definition_version.experiment_id, definition_version_id=definition_version.id,
        contract_version_id=contract.id, unit_of_assignment="visitor", allocation_design="50/50 split.",
        client_request_id=uuid.uuid4().hex,
    )
    values.update(overrides)
    return ExecutionAuthorization(**values)


def _snapshot_row(authorization: ExecutionAuthorization, variant: ExperimentVariant, **overrides) -> ExecutionAuthorizationVariant:
    values = dict(
        workspace_id=authorization.workspace_id, authorization_id=authorization.id,
        experiment_id=authorization.experiment_id, variant_id=variant.id,
    )
    values.update(overrides)
    return ExecutionAuthorizationVariant(**values)


def _violation(session, row) -> str:
    with pytest.raises(IntegrityError) as excinfo:
        with session.begin_nested():
            session.add(row)
            session.flush()
    return excinfo.value.orig.diag.constraint_name


def _ready(session, campaign, experiment, actor):
    version = _version(session, campaign, experiment, actor)
    variant = _variant(session, campaign, experiment, actor, version)
    contract = _contract(session, campaign, experiment, actor, version)
    return version, variant, contract


def test_duplicate_workspace_client_request_id_is_rejected_by_the_database(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version, _variant, contract = _ready(db_session, campaign, experiment, actor)
    db_session.add(_authorization_row(definition_version=version, contract=contract, client_request_id="shared"))
    db_session.flush()
    assert _violation(
        db_session, _authorization_row(definition_version=version, contract=contract, client_request_id="shared")
    ) == "uq_execution_authorizations_workspace_client_request_id"


def test_second_active_authorization_for_the_same_experiment_is_rejected_by_the_partial_index(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version, _variant, contract = _ready(db_session, campaign, experiment, actor)
    db_session.add(_authorization_row(definition_version=version, contract=contract))
    db_session.flush()
    assert _violation(db_session, _authorization_row(definition_version=version, contract=contract)) == (
        "uq_execution_authorizations_experiment_active"
    )


def test_a_revoked_predecessor_does_not_block_a_new_active_row(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version, _variant, contract = _ready(db_session, campaign, experiment, actor)
    from datetime import datetime, timezone

    first = _authorization_row(definition_version=version, contract=contract)
    db_session.add(first)
    db_session.flush()
    first.revoked_at = datetime.now(timezone.utc)
    first.revoked_reason = "done"
    db_session.flush()
    db_session.add(_authorization_row(definition_version=version, contract=contract))
    db_session.flush()  # accepted — only one ACTIVE row is constrained


def test_composite_fk_rejects_a_workspace_that_does_not_own_the_pinned_experiment(db_session) -> None:
    c1, _s1, _h1, first, a1 = build_current_experiment(db_session, campaign_name="Tenant One")
    _c2, _s2, _h2, second, _a2 = build_current_experiment(db_session, campaign_name="Tenant Two")
    version, _variant, contract = _ready(db_session, c1, first, a1)
    # workspace_id is shared by all three composite FKs; the experiment one
    # (defined first) is the one PostgreSQL reports.
    assert _violation(db_session, _authorization_row(definition_version=version, contract=contract, workspace_id=second.workspace_id)) == (
        "fk_execution_authorizations_experiment_workspace"
    )


def test_composite_fk_rejects_a_definition_that_does_not_exist(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version, _variant, contract = _ready(db_session, campaign, experiment, actor)
    assert _violation(db_session, _authorization_row(definition_version=version, contract=contract, definition_version_id=uuid.uuid4())) == (
        "fk_execution_authorizations_definition_version_workspace"
    )


def test_composite_fk_rejects_a_contract_that_does_not_exist(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version, _variant, contract = _ready(db_session, campaign, experiment, actor)
    assert _violation(db_session, _authorization_row(definition_version=version, contract=contract, contract_version_id=uuid.uuid4())) == (
        "fk_execution_authorizations_contract_version_workspace"
    )


def test_composite_fk_rejects_an_experiment_that_does_not_own_the_pinned_contract(db_session) -> None:
    c1, _s1, _h1, first, a1 = build_current_experiment(db_session, campaign_name="Tenant One")
    _c2, _s2, _h2, second, _a2 = build_current_experiment(db_session, campaign_name="Tenant Two")
    version, _variant, contract = _ready(db_session, c1, first, a1)
    assert _violation(db_session, _authorization_row(definition_version=version, contract=contract, experiment_id=second.id)) == (
        "fk_execution_authorizations_experiment_workspace"
    )


def test_self_fk_rejects_a_nonexistent_successor(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version, _variant, contract = _ready(db_session, campaign, experiment, actor)
    assert _violation(
        db_session,
        _authorization_row(
            definition_version=version, contract=contract, revoked_at=dt.datetime.now(dt.timezone.utc),
            revoked_reason="x", superseded_by_execution_authorization_id=uuid.uuid4(),
        ),
    ) == "fk_execution_authorizations_superseded_by_id"


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"unit_of_assignment": "   "}, "ck_execution_authorizations_text_fields_nonblank"),
        ({"unit_of_assignment": ""}, "ck_execution_authorizations_text_fields_nonblank"),
        ({"allocation_design": "   "}, "ck_execution_authorizations_text_fields_nonblank"),
        ({"allocation_design": ""}, "ck_execution_authorizations_text_fields_nonblank"),
    ],
)
def test_database_check_constraints_reject_invalid_authorization_rows(db_session, overrides, expected) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version, _variant, contract = _ready(db_session, campaign, experiment, actor)
    assert _violation(db_session, _authorization_row(definition_version=version, contract=contract, **overrides)) == expected


def test_revocation_pairing_check_rejects_one_sided_disposition(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version, _variant, contract = _ready(db_session, campaign, experiment, actor)
    assert _violation(
        db_session, _authorization_row(definition_version=version, contract=contract, revoked_at=dt.datetime.now(dt.timezone.utc))
    ) == "ck_execution_authorizations_revocation_pairing"
    assert _violation(
        db_session, _authorization_row(definition_version=version, contract=contract, revoked_reason="x")
    ) == "ck_execution_authorizations_revocation_pairing"


def test_duplicate_variant_in_one_snapshot_is_rejected_by_the_database(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version, variant, contract = _ready(db_session, campaign, experiment, actor)
    authorization = _authorization_row(definition_version=version, contract=contract)
    db_session.add(authorization)
    db_session.flush()
    db_session.add(_snapshot_row(authorization, variant))
    db_session.flush()
    assert _violation(db_session, _snapshot_row(authorization, variant)) == (
        "uq_execution_authorization_variants_authorization_variant"
    )


def test_composite_fk_rejects_a_variant_that_does_not_belong_to_the_authorized_experiment(db_session) -> None:
    c1, _s1, _h1, first, a1 = build_current_experiment(db_session, campaign_name="Tenant One")
    c2, _s2, _h2, second, a2 = build_current_experiment(db_session, campaign_name="Tenant Two")
    version1, _v1, contract1 = _ready(db_session, c1, first, a1)
    _version2, variant2, _contract2 = _ready(db_session, c2, second, a2)
    authorization = _authorization_row(definition_version=version1, contract=contract1)
    db_session.add(authorization)
    db_session.flush()
    assert _violation(db_session, _snapshot_row(authorization, variant2)) == (
        "fk_execution_authorization_variants_variant_workspace"
    )


def test_no_maximum_variant_snapshot_cardinality_is_enforced(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _version(db_session, campaign, experiment, actor)
    contract = _contract(db_session, campaign, experiment, actor, version)
    variants = [_variant(db_session, campaign, experiment, actor, version, label=f"V{i}", key=f"v-{i}") for i in range(5)]
    authorization = _authorization_row(definition_version=version, contract=contract)
    db_session.add(authorization)
    db_session.flush()
    for variant in variants:
        db_session.add(_snapshot_row(authorization, variant))
    db_session.flush()  # accepted by the database — no maximum


def test_minimum_variant_cardinality_is_not_database_enforced(db_session) -> None:
    """The OBSERVATIONAL>=1/CONTROLLED>=2 rule is SERVICE-level only — the
    database itself accepts an Authorization with a ZERO-Variant snapshot,
    the same honest-limit class as MVP39B-OBS-1."""
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version, _variant, contract = _ready(db_session, campaign, experiment, actor)
    db_session.add(_authorization_row(definition_version=version, contract=contract))
    db_session.flush()  # accepted by the database, zero snapshot rows


def test_columns_are_exactly_the_frozen_set_and_identifiers_fit_postgresql() -> None:
    for table, forbidden in (
        (
            ExecutionAuthorization.__table__,
            {
                "status", "execution_started", "assignment_started", "exposure_started", "tracking_valid",
                "measurement_ready", "result", "winner", "loser", "validity", "causality", "created_by", "updated_at",
            },
        ),
        (
            ExecutionAuthorizationVariant.__table__,
            {"ordinal", "label", "condition_description", "public_id", "created_by", "updated_at"},
        ),
    ):
        for fk in table.foreign_key_constraints:
            assert fk.ondelete is None and fk.onupdate is None
            assert len(fk.name) <= 63
        names = [c.name for c in table.constraints if c.name] + [i.name for i in table.indexes]
        assert all(len(str(name)) <= 63 for name in names), [n for n in names if len(str(n)) > 63]
        assert not forbidden & set(table.columns.keys())
    assert set(ExecutionAuthorization.__table__.columns.keys()) == {
        "id", "public_id", "workspace_id", "experiment_id", "definition_version_id", "contract_version_id",
        "unit_of_assignment", "allocation_design", "client_request_id", "revoked_at", "revoked_reason",
        "superseded_by_execution_authorization_id", "created_at",
    }
    assert set(ExecutionAuthorizationVariant.__table__.columns.keys()) == {
        "id", "workspace_id", "authorization_id", "experiment_id", "variant_id", "created_at",
    }
    assert "fk_execution_authorizations_definition_version_workspace" in {
        c.name for c in ExecutionAuthorization.__table__.constraints
    }
    assert "fk_execution_authorization_variants_variant_workspace" in {
        c.name for c in ExecutionAuthorizationVariant.__table__.constraints
    }


def test_experiment_variant_gained_exactly_the_frozen_new_candidate_key() -> None:
    names = {c.name for c in ExperimentVariant.__table__.constraints if c.name}
    assert "uq_experiment_variants_id_experiment_workspace" in names


def test_audit_link_column_is_a_nullable_fk_to_the_authorization_table(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    event = AuditEvent(
        public_id=generate_public_id("AUDT"), workspace_id=campaign.workspace_id,
        event_type="strategy.execution_authorization.authorized", actor_type=ActorType.USER, actor_user_id=actor.id,
        execution_authorization_id=uuid.uuid4(),
    )
    assert _violation(db_session, event) == "fk_audit_events_execution_authorization_id"
    ok = AuditEvent(
        public_id=generate_public_id("AUDT"), workspace_id=campaign.workspace_id,
        event_type="unrelated.event", actor_type=ActorType.SYSTEM,
    )
    db_session.add(ok)
    db_session.flush()
    assert ok.execution_authorization_id is None


def test_atomicity_a_failing_snapshot_row_rolls_back_the_already_flushed_authorization_row_too(db_session) -> None:
    """Real, non-mocked proof: ``ExecutionAuthorizationRepository.create()``
    builds the Authorization row and every Variant-snapshot row as one
    aggregate unit — the Authorization row is flushed first (to obtain its
    id for the children's FK), but a child violating a DB constraint must
    roll back that already-flushed Authorization row too, leaving zero
    partial rows of either table."""
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version, variant, contract = _ready(db_session, campaign, experiment, actor)
    repo = ExecutionAuthorizationRepository(db_session)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            repo.create(
                experiment_id=version.experiment_id, workspace_id=version.workspace_id,
                definition_version_id=version.id, contract_version_id=contract.id,
                variant_ids=[variant.id, variant.id],  # duplicate -> uq_..._authorization_variant violation
                unit_of_assignment="visitor", allocation_design="x", client_request_id=uuid.uuid4().hex,
            )
    authorization_count = db_session.scalar(
        select(func.count()).select_from(ExecutionAuthorization).where(
            ExecutionAuthorization.experiment_id == version.experiment_id
        )
    )
    snapshot_count = db_session.scalar(
        select(func.count()).select_from(ExecutionAuthorizationVariant).where(
            ExecutionAuthorizationVariant.experiment_id == version.experiment_id
        )
    )
    assert authorization_count == 0 and snapshot_count == 0  # nothing partial persisted


def test_active_and_history_read_methods(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version, variant, contract = _ready(db_session, campaign, experiment, actor)
    repo = ExecutionAuthorizationRepository(db_session)
    row, snapshot = repo.create(
        experiment_id=version.experiment_id, workspace_id=version.workspace_id,
        definition_version_id=version.id, contract_version_id=contract.id, variant_ids=[variant.id],
        unit_of_assignment="visitor", allocation_design="x", client_request_id=uuid.uuid4().hex,
    )
    db_session.flush()
    active = repo.get_active_for_experiment(experiment_id=version.experiment_id, workspace_id=version.workspace_id)
    assert active.id == row.id
    assert repo.exists_active_for_contract_version(contract_version_id=contract.id) is True
    history = repo.list_for_experiment(experiment_id=version.experiment_id, workspace_id=version.workspace_id)
    assert [h.id for h in history] == [row.id]
    assert [s.variant_id for s in repo.list_variants_for_authorization(authorization_id=row.id)] == [variant.id]
