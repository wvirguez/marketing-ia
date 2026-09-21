"""Structural database backstops for ExperimentEvidenceClaim (frozen Experiment
Evidence Binding Design Freeze): every test here BYPASSES the service and
inserts directly, proving the database itself — not application code — rejects
the violation. Each load-bearing composite FK is exercised in isolation (the
row is built so ONLY that FK is violated). All marked `postgres`.

Honest limit, documented rather than hidden: the campaign match of the
MetricEntry to the Experiment's campaign is SERVICE-enforced only
(EEB-DF-OBS-1) — ``Experiment`` has no ``campaign_id`` and an FK to Strategy
would invert the canonical lock order — so no DB test claims it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.audit.models import ActorType, AuditEvent
from app.core.ids import generate_public_id
from app.measurement.models import MetricValue
from app.strategy.execution_authorization_service import ExperimentExecutionAuthorizationService
from app.strategy.models import ExperimentEvidenceClaim
from app.strategy.repository import MeasurementContractRepository
from tests.evidenceclaimtest import (
    Started,
    _signal_payload,
    build_started,
    claim,
    declare_contract,
    make_entry,
    revoke,
    start_authorization,
)
from tests.test_execution_authorization_domain import _authorize

pytestmark = pytest.mark.postgres


def _row(started: Started, entry, **overrides) -> ExperimentEvidenceClaim:
    values = dict(
        public_id=generate_public_id("ECL"),
        workspace_id=started.campaign.workspace_id,
        start_id=started.start.id,
        authorization_id=started.authorization.id,
        experiment_id=started.experiment.id,
        contract_version_id=started.authorization.contract_version_id,
        required_signal_id=started.signal.id,
        metric_entry_id=entry.id,
        metric_name="clicks",
        client_request_id=uuid.uuid4().hex,
        claimed_by_user_id=started.actor.id,
    )
    values.update(overrides)
    return ExperimentEvidenceClaim(**values)


def _violation(session, row) -> str:
    with pytest.raises(IntegrityError) as excinfo:
        with session.begin_nested():
            session.add(row)
            session.flush()
    return excinfo.value.orig.diag.constraint_name


def _two_experiments(db_session):
    first = build_started(db_session)
    second = build_started(
        db_session, campaign_name="Second Claim Campaign", within_workspace_id=first.campaign.workspace_id, suffix="-2"
    )
    return first, second


# --- the baseline row is valid ------------------------------------------------------------------------------------------


def test_a_well_formed_raw_row_is_accepted(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    db_session.add(_row(started, entry))
    db_session.flush()  # the helper row itself is valid: every violation below is therefore the tested one


# --- FK1: the Start belongs to the named Authorization ------------------------------------------------------------------


def test_fk1_rejects_a_start_paired_with_another_experiments_authorization(db_session) -> None:
    first, second = _two_experiments(db_session)
    entry = make_entry(db_session, first.campaign)
    # FK2/FK3/FK4/FK5 all hold for `first`; ONLY (start_id, authorization_id, workspace_id) names no real Start.
    assert _violation(db_session, _row(first, entry, start_id=second.start.id)) == (
        "fk_experiment_evidence_claims_start_authorization"
    )


def test_fk1_rejects_a_nonexistent_start(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    assert _violation(db_session, _row(started, entry, start_id=uuid.uuid4())) == (
        "fk_experiment_evidence_claims_start_authorization"
    )


# --- FK2: the Authorization pins THIS Contract within THIS Experiment ------------------------------------------------


def test_fk2_rejects_an_authorization_from_a_different_experiment(db_session) -> None:
    first, second = _two_experiments(db_session)
    entry = make_entry(db_session, first.campaign)
    # A fully consistent attempt of SECOND (start, authorization, contract, signal, experiment) except
    # the authorization/start pair stays FIRST's: the claim then says experiment=SECOND with FIRST's authorization.
    row = _row(
        first, entry,
        experiment_id=second.experiment.id,
        contract_version_id=second.authorization.contract_version_id,
        required_signal_id=second.signal.id,  # FK3 holds for SECOND
    )
    assert _violation(db_session, row) == "fk_experiment_evidence_claims_authorization_contract"


def test_fk2_rejects_a_contract_the_authorization_does_not_pin(db_session) -> None:
    """Same Experiment: v1 authorized -> revoked -> v2 -> authorized -> started. The claim names v1 (its signal is
    v1's, so FK3 holds) but the Authorization pins v2: only FK2 can reject."""
    from tests.strategytest import build_current_experiment
    from tests.test_execution_authorization_domain import _declare_variant, _define

    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)
    _declare_variant(db_session, campaign, experiment, actor, version)
    contract_one = declare_contract(
        db_session, campaign, experiment, actor, version, signals=[_signal_payload("Old")], key="c-1"
    )
    _authorize(db_session, campaign, experiment, actor, key="a-1")
    ExperimentExecutionAuthorizationService(db_session).revoke(
        campaign=campaign, experiment_public_id=experiment.public_id, reason="Redo.", actor_user_id=actor.id
    )
    declare_contract(
        db_session, campaign, experiment, actor, version, signals=[_signal_payload("New")], key="c-2", base_version=1
    )
    authorization, _snap, _c = _authorize(db_session, campaign, experiment, actor, key="a-2")
    _a, start, _created = start_authorization(db_session, campaign, experiment, actor, authorization)
    old_signal = MeasurementContractRepository(db_session).list_signals_for_version(contract_version_id=contract_one.id)[0]
    new_signal = MeasurementContractRepository(db_session).list_signals_for_version(
        contract_version_id=authorization.contract_version_id
    )[0]
    started = Started(campaign, experiment, actor, version, authorization, start, [new_signal])
    entry = make_entry(db_session, campaign)
    row = _row(started, entry, contract_version_id=contract_one.id, required_signal_id=old_signal.id)
    assert _violation(db_session, row) == "fk_experiment_evidence_claims_authorization_contract"
    # ...and with the claim naming the PINNED contract but the OLD signal, only FK3 rejects.
    row = _row(started, entry, required_signal_id=old_signal.id)
    assert _violation(db_session, row) == "fk_experiment_evidence_claims_signal_contract"


# --- FK3: the RequiredSignal belongs to THIS Contract within THIS Experiment ------------------------------------------


def test_fk3_rejects_a_signal_of_a_different_experiment(db_session) -> None:
    first, second = _two_experiments(db_session)
    entry = make_entry(db_session, first.campaign)
    assert _violation(db_session, _row(first, entry, required_signal_id=second.signal.id)) == (
        "fk_experiment_evidence_claims_signal_contract"
    )


def test_fk3_rejects_a_nonexistent_signal(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    assert _violation(db_session, _row(started, entry, required_signal_id=uuid.uuid4())) == (
        "fk_experiment_evidence_claims_signal_contract"
    )


# --- FK4/FK5: the datum ------------------------------------------------------------------------------------------------


def test_fk4_rejects_a_metric_entry_of_another_workspace(db_session) -> None:
    started = build_started(db_session)
    other = build_started(db_session, campaign_name="Other Tenant")
    assert other.campaign.workspace_id != started.campaign.workspace_id
    foreign_entry = make_entry(db_session, other.campaign)  # its (entry, "clicks") pair is real, so FK5 holds
    assert _violation(db_session, _row(started, foreign_entry)) == "fk_experiment_evidence_claims_metric_entry_workspace"


def test_fk5_rejects_a_metric_name_absent_from_the_entry(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    assert _violation(db_session, _row(started, entry, metric_name="reach")) == (
        "fk_experiment_evidence_claims_metric_value"
    )
    assert _violation(db_session, _row(started, entry, metric_name="Clicks")) == (
        "fk_experiment_evidence_claims_metric_value"
    )  # exact match, no normalization


def test_fk5_rejects_a_metric_name_that_exists_only_in_another_entry(db_session) -> None:
    started = build_started(db_session)
    with_reach = make_entry(db_session, started.campaign, values={"reach": Decimal("1")})
    clicks_only = make_entry(db_session, started.campaign)
    assert _violation(db_session, _row(started, clicks_only, metric_name="reach")) == (
        "fk_experiment_evidence_claims_metric_value"
    )
    db_session.add(_row(started, with_reach, metric_name="reach"))
    db_session.flush()


def test_a_workspace_that_differs_from_every_parent_is_rejected(db_session) -> None:
    started = build_started(db_session)
    other = build_started(db_session, campaign_name="Other Tenant")
    entry = make_entry(db_session, started.campaign)
    name = _violation(db_session, _row(started, entry, workspace_id=other.campaign.workspace_id))
    assert name.startswith("fk_experiment_evidence_claims_"), name


# --- identity / uniqueness / lifecycle ---------------------------------------------------------------------------------


def test_the_active_datum_partial_unique_index_rejects_a_second_active_claim(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    db_session.add(_row(started, entry))
    db_session.flush()
    assert _violation(db_session, _row(started, entry)) == "uq_experiment_evidence_claims_active_datum"


def test_a_disposed_claim_does_not_block_a_new_active_claim_of_the_same_material(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    disposed = _row(
        started, entry, disposed_at=dt.datetime.now(dt.timezone.utc), disposed_by_user_id=started.actor.id,
        disposal_reason="Superseded intent.",
    )
    db_session.add(disposed)
    db_session.flush()
    db_session.add(_row(started, entry))  # active again: allowed
    db_session.flush()
    assert _violation(db_session, _row(started, entry)) == "uq_experiment_evidence_claims_active_datum"


def test_the_same_datum_for_a_different_signal_or_start_is_not_a_duplicate(db_session) -> None:
    started = build_started(db_session, signal_names=("A", "B"))
    entry = make_entry(db_session, started.campaign)
    db_session.add(_row(started, entry, required_signal_id=started.signals[0].id))
    db_session.add(_row(started, entry, required_signal_id=started.signals[1].id))
    db_session.flush()
    revoke(db_session, started)
    authorization, _snap, _c = _authorize(db_session, started.campaign, started.experiment, started.actor, key="a-2")
    _a, second_start, _created = start_authorization(
        db_session, started.campaign, started.experiment, started.actor, authorization, key="s-2"
    )
    db_session.add(
        _row(started, entry, start_id=second_start.id, authorization_id=authorization.id)
    )  # a different attempt: not a duplicate
    db_session.flush()


def test_duplicate_workspace_client_request_id_is_rejected(db_session) -> None:
    started = build_started(db_session)
    first_entry = make_entry(db_session, started.campaign)
    second_entry = make_entry(db_session, started.campaign)
    db_session.add(_row(started, first_entry, client_request_id="shared"))
    db_session.flush()
    assert _violation(db_session, _row(started, second_entry, client_request_id="shared")) == (
        "uq_experiment_evidence_claims_workspace_client_request_id"
    )


def test_the_disposal_triple_is_all_null_or_all_set(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    now = dt.datetime.now(dt.timezone.utc)
    partials = (
        {"disposed_at": now},
        {"disposed_by_user_id": started.actor.id},
        {"disposal_reason": "why"},
        {"disposed_at": now, "disposed_by_user_id": started.actor.id},
        {"disposed_at": now, "disposal_reason": "why"},
        {"disposed_by_user_id": started.actor.id, "disposal_reason": "why"},
    )
    for partial in partials:
        assert _violation(db_session, _row(started, entry, **partial)) == (
            "ck_experiment_evidence_claims_disposal_complete"
        ), partial


def test_a_blank_disposal_reason_is_rejected(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    row = _row(
        started, entry, disposed_at=dt.datetime.now(dt.timezone.utc), disposed_by_user_id=started.actor.id,
        disposal_reason="   ",
    )
    assert _violation(db_session, row) == "ck_experiment_evidence_claims_disposal_reason_nonblank"


# --- MV1: metric_values natural identity --------------------------------------------------------------------------------


def test_metric_values_reject_a_duplicate_name_within_one_entry(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    assert _violation(
        db_session, MetricValue(metric_entry_id=entry.id, metric_name="clicks", value=Decimal("1"))
    ) == "uq_metric_values_metric_entry_id_metric_name"
    other = make_entry(db_session, started.campaign)  # the same name in ANOTHER entry is fine
    db_session.add(MetricValue(metric_entry_id=other.id, metric_name="reach", value=Decimal("1")))
    db_session.flush()


# --- candidate keys / table shape -----------------------------------------------------------------------------------------


def _unique_columns(inspector, table) -> dict[str, list[str]]:
    return {u["name"]: u["column_names"] for u in inspector.get_unique_constraints(table)}


def test_exactly_the_four_frozen_candidate_keys_exist_with_safe_names(db_session) -> None:
    inspector = inspect(db_session.get_bind())
    assert _unique_columns(inspector, "metric_values")["uq_metric_values_metric_entry_id_metric_name"] == [
        "metric_entry_id", "metric_name",
    ]
    assert _unique_columns(inspector, "measurement_contract_signals")[
        "uq_contract_signals_id_contract_experiment_workspace"
    ] == ["id", "contract_version_id", "experiment_id", "workspace_id"]
    assert _unique_columns(inspector, "execution_authorizations")[
        "uq_execution_authorizations_id_contract_experiment_workspace"
    ] == ["id", "contract_version_id", "experiment_id", "workspace_id"]
    assert _unique_columns(inspector, "execution_start_attestations")[
        "uq_execution_start_attestations_id_authorization_workspace"
    ] == ["id", "authorization_id", "workspace_id"]
    for table in (
        "metric_values", "measurement_contract_signals", "execution_authorizations",
        "execution_start_attestations", "experiment_evidence_claims",
    ):
        assert all(len(name) <= 63 for name in _unique_columns(inspector, table)), table


def test_the_claim_table_has_exactly_the_frozen_constraints(db_session) -> None:
    inspector = inspect(db_session.get_bind())
    table = "experiment_evidence_claims"
    fks = {fk["name"]: (fk["referred_table"], fk["constrained_columns"], fk["referred_columns"]) for fk in inspector.get_foreign_keys(table)}
    assert fks["fk_experiment_evidence_claims_start_authorization"] == (
        "execution_start_attestations", ["start_id", "authorization_id", "workspace_id"],
        ["id", "authorization_id", "workspace_id"],
    )
    assert fks["fk_experiment_evidence_claims_authorization_contract"] == (
        "execution_authorizations", ["authorization_id", "contract_version_id", "experiment_id", "workspace_id"],
        ["id", "contract_version_id", "experiment_id", "workspace_id"],
    )
    assert fks["fk_experiment_evidence_claims_signal_contract"] == (
        "measurement_contract_signals", ["required_signal_id", "contract_version_id", "experiment_id", "workspace_id"],
        ["id", "contract_version_id", "experiment_id", "workspace_id"],
    )
    assert fks["fk_experiment_evidence_claims_metric_entry_workspace"] == (
        "metric_entries", ["metric_entry_id", "workspace_id"], ["id", "workspace_id"],
    )
    assert fks["fk_experiment_evidence_claims_metric_value"] == (
        "metric_values", ["metric_entry_id", "metric_name"], ["metric_entry_id", "metric_name"],
    )
    assert {n for n in fks} == {
        "fk_experiment_evidence_claims_start_authorization", "fk_experiment_evidence_claims_authorization_contract",
        "fk_experiment_evidence_claims_signal_contract", "fk_experiment_evidence_claims_metric_entry_workspace",
        "fk_experiment_evidence_claims_metric_value", "fk_experiment_evidence_claims_workspace_id_workspaces",
        "fk_experiment_evidence_claims_claimed_by_user_id_users",
        "fk_experiment_evidence_claims_disposed_by_user_id_users",
    }
    assert all(len(name) <= 63 for name in fks)
    # No strategy/campaign/variant reference of any kind, and no cascade anywhere.
    assert not any(referred[0] in {"strategies", "campaigns", "experiment_variants", "hypotheses"} for referred in fks.values())
    assert all(fk["options"].get("ondelete") != "CASCADE" for fk in inspector.get_foreign_keys(table))
    assert set(_unique_columns(inspector, table)) == {"uq_experiment_evidence_claims_workspace_client_request_id"}
    assert {c["name"] for c in inspector.get_check_constraints(table)} == {
        "ck_experiment_evidence_claims_disposal_complete", "ck_experiment_evidence_claims_disposal_reason_nonblank",
    }
    indexes = {i["name"]: i for i in inspector.get_indexes(table)}
    assert indexes["uq_experiment_evidence_claims_active_datum"]["unique"] is True
    assert indexes["uq_experiment_evidence_claims_active_datum"]["column_names"] == [
        "start_id", "required_signal_id", "metric_entry_id", "metric_name",
    ]
    assert all(len(name) <= 63 for name in indexes)


# --- audit FK ------------------------------------------------------------------------------------------------------------------


def test_the_audit_event_claim_fk_rejects_a_nonexistent_claim(db_session) -> None:
    started = build_started(db_session)
    event = AuditEvent(
        public_id=generate_public_id("AUDT"), workspace_id=started.campaign.workspace_id,
        event_type="strategy.evidence_claim.claimed", actor_type=ActorType.USER,
        experiment_evidence_claim_id=uuid.uuid4(),
    )
    assert _violation(db_session, event) == "fk_audit_events_experiment_evidence_claim_id"


def test_the_audit_event_carries_the_claim_fk_column(db_session) -> None:
    started = build_started(db_session)
    row, _ = claim(db_session, started, entry=make_entry(db_session, started.campaign))
    stored = db_session.execute(
        select(AuditEvent).where(AuditEvent.experiment_evidence_claim_id == row.id)
    ).scalars().all()
    assert len(stored) == 1 and stored[0].event_type == "strategy.evidence_claim.claimed"
