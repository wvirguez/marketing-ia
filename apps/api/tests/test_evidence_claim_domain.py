"""Service-level tests for Experiment Evidence Binding (frozen Experiment
Evidence Binding Design Freeze): creation, the frozen error precedence and
non-leaky tenancy, idempotency, duplicate identity, disposal, revocation (R2),
reauthorization, corrections (C1), the temporal (T1) / tracking / Variant
firewalls, distribution-owned entries (D1), audit semantics, the semantic
firewall and real-persistence non-effects. All marked `postgres`.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import func, inspect, select

from app.audit.models import ActorType, AuditEvent
from app.core.api_errors import (
    EvidenceClaimAlreadyActiveError,
    EvidenceClaimAlreadyDisposedError,
    EvidenceClaimMetricNotInEntryError,
    EvidenceClaimSignalNotInPinnedContractError,
    ForbiddenError,
    IdempotencyKeyConflictError,
)
from app.persistence.base import Base
from app.strategy.execution_authorization_service import ExperimentExecutionAuthorizationService
from app.strategy.experiment_evidence_claim_service import (
    EVENT_EVIDENCE_CLAIM_CLAIMED,
    EVENT_EVIDENCE_CLAIM_DISPOSED,
    ExperimentEvidenceClaimService,
)
from app.strategy.models import ExperimentEvidenceClaim, Hypothesis
from app.strategy.repository import ExperimentEvidenceClaimRepository
from app.strategy.schemas import (
    EVIDENCE_CLAIM_SEMANTICS,
    CreateEvidenceClaimRequest,
    DisposeEvidenceClaimRequest,
    EvidenceClaimPublic,
    evidence_claim_to_public,
)
from tests.contenttest import make_user
from tests.evidenceclaimtest import (
    build_distribution_in_campaign,
    build_started,
    claim,
    correct_distribution_evidence,
    declare_contract,
    dispose,
    make_distribution_evidence,
    make_entry,
    revoke,
    start_authorization,
    utc,
    _signal_payload,
)
from tests.test_execution_authorization_domain import _authorize

pytestmark = pytest.mark.postgres


def _events(session, started, event_type) -> list[AuditEvent]:
    """Scoped to THIS test's Experiment: other suites commit their own rows to the shared database."""
    return list(
        session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == event_type, AuditEvent.experiment_id == started.experiment.id
            )
        ).scalars()
    )


def _claim_count(session, started) -> int:
    return session.scalar(
        select(func.count())
        .select_from(ExperimentEvidenceClaim)
        .where(ExperimentEvidenceClaim.experiment_id == started.experiment.id)
    )


def _table_counts(session) -> dict[str, int]:
    existing = set(inspect(session.get_bind()).get_table_names())
    return {
        table.name: session.scalar(select(func.count()).select_from(table))
        for table in Base.metadata.sorted_tables
        if table.name in existing
    }


def _view(session, started, row):
    return evidence_claim_to_public(
        ExperimentEvidenceClaimService(session).view_for_claim(row), experiment_public_id=started.experiment.public_id
    )


# --- creation / semantics ----------------------------------------------------------------------------


def test_create_persists_one_claim_with_derived_integrity_columns(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    row, created = claim(db_session, started, entry=entry)
    assert created is True
    assert row.public_id.startswith("ECL-") and len(row.public_id) == 16
    assert row.start_id == started.start.id and row.authorization_id == started.authorization.id
    assert row.experiment_id == started.experiment.id
    assert row.contract_version_id == started.authorization.contract_version_id == started.signal.contract_version_id
    assert row.required_signal_id == started.signal.id
    assert (row.metric_entry_id, row.metric_name) == (entry.id, "clicks")
    assert row.workspace_id == started.campaign.workspace_id and row.claimed_by_user_id == started.actor.id
    assert row.created_at is not None
    assert (row.disposed_at, row.disposed_by_user_id, row.disposal_reason) == (None, None, None)
    assert _claim_count(db_session, started) == 1


def test_the_claim_copies_no_datum_value_period_channel_or_source(db_session) -> None:
    columns = {c.name for c in ExperimentEvidenceClaim.__table__.columns}
    assert not {
        "value", "metric_value", "period_start", "period_end", "channel", "source", "campaign_id", "variant_id",
        "assignment_id", "exposure_id", "eligible", "eligibility", "valid", "validity", "status", "result", "winner",
        "verdict", "note", "is_current", "later_correction_exists", "tracking_valid",
    } & columns
    assert columns == {
        "id", "public_id", "workspace_id", "start_id", "authorization_id", "experiment_id", "contract_version_id",
        "required_signal_id", "metric_entry_id", "metric_name", "client_request_id", "claimed_by_user_id",
        "created_at", "disposed_at", "disposed_by_user_id", "disposal_reason",
    }


def test_create_writes_exactly_one_user_audit_event_with_the_frozen_shape(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    row, _ = claim(db_session, started, entry=entry)
    events = _events(db_session, started, EVENT_EVIDENCE_CLAIM_CLAIMED)
    assert len(events) == 1
    event = events[0]
    assert event.actor_type is ActorType.USER and event.actor_user_id == started.actor.id
    assert (event.previous_state, event.new_state) == (None, f"claimed:{row.public_id}")
    assert event.experiment_evidence_claim_id == row.id
    assert event.campaign_id == started.campaign.id and event.experiment_id == started.experiment.id
    assert event.execution_authorization_id == started.authorization.id
    assert event.execution_start_attestation_id == started.start.id
    # Frozen: NO Strategy/Hypothesis/MetricEntry/signal FK on the event (lock order + claim is the provenance).
    assert event.strategy_id is None and event.hypothesis_id is None and event.metric_entry_id is None
    assert event.performance_signal_id is None and event.measurement_contract_id is None


# --- idempotency ---------------------------------------------------------------------------------------------


def test_a_matching_replay_returns_the_original_claim_and_writes_nothing(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    first, created = claim(db_session, started, entry=entry, key="k-1")
    second, replayed_created = claim(db_session, started, entry=entry, key="k-1")
    assert created is True and replayed_created is False and second.id == first.id
    assert _claim_count(db_session, started) == 1
    assert len(_events(db_session, started, EVENT_EVIDENCE_CLAIM_CLAIMED)) == 1


@pytest.mark.parametrize("field", ["metric_name", "entry", "signal"])
def test_the_same_key_with_different_material_is_an_idempotency_conflict(db_session, field) -> None:
    started = build_started(db_session, signal_names=("Signal A", "Signal B"))
    entry = make_entry(db_session, started.campaign)
    other_entry = make_entry(db_session, started.campaign)
    claim(db_session, started, entry=entry, key="k-1")
    kwargs = {"entry": entry, "key": "k-1"}
    if field == "metric_name":
        kwargs["metric_name"] = "impressions"
    elif field == "entry":
        kwargs["entry"] = other_entry
    else:
        kwargs["signal"] = started.signals[1]
    with pytest.raises(IdempotencyKeyConflictError):
        claim(db_session, started, **kwargs)
    assert _claim_count(db_session, started) == 1


def test_the_same_key_under_a_different_start_is_an_idempotency_conflict(db_session) -> None:
    """The Start (the execution attempt) is part of the material: the same key + signal + datum under ANOTHER
    attempt is a different request, never a replay of the first attempt's claim."""
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    claim(db_session, started, entry=entry, key="k-1")
    revoke(db_session, started)
    second_authorization, _snap, _c = _authorize(
        db_session, started.campaign, started.experiment, started.actor, key="a-2"
    )
    _a, second_start, _created = start_authorization(
        db_session, started.campaign, started.experiment, started.actor, second_authorization, key="s-2"
    )
    with pytest.raises(IdempotencyKeyConflictError):
        claim(db_session, started, entry=entry, key="k-1", start=second_start)
    assert _claim_count(db_session, started) == 1


def test_the_same_key_with_an_unresolvable_signal_is_a_key_conflict_not_a_leak(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    claim(db_session, started, entry=entry, key="k-1")
    with pytest.raises(IdempotencyKeyConflictError):
        ExperimentEvidenceClaimService(db_session).create(
            campaign=started.campaign, experiment_public_id=started.experiment.public_id,
            start_public_id=started.start.public_id, client_request_id="k-1",
            required_signal_public_id="RSG-unknown0000", metric_entry_public_id=entry.public_id,
            metric_name="clicks", actor_user_id=started.actor.id,
        )


def test_a_replay_after_disposal_returns_the_original_in_its_current_state(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    first, _ = claim(db_session, started, entry=entry, key="k-1")
    dispose(db_session, started, first)
    replay, created = claim(db_session, started, entry=entry, key="k-1")
    assert created is False and replay.id == first.id and replay.disposed_at is not None
    assert _claim_count(db_session, started) == 1


def test_a_replay_after_revocation_returns_the_original_without_revalidation(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    first, _ = claim(db_session, started, entry=entry, key="k-1")
    revoke(db_session, started)
    replay, created = claim(db_session, started, entry=entry, key="k-1")
    assert created is False and replay.id == first.id


# --- duplicate identity ----------------------------------------------------------------------------------------------


def test_a_different_key_for_the_same_active_material_is_already_active(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    claim(db_session, started, entry=entry)
    with pytest.raises(EvidenceClaimAlreadyActiveError):
        claim(db_session, started, entry=entry)
    assert _claim_count(db_session, started) == 1


def test_after_disposal_the_same_material_is_a_new_claim_identity(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    first, _ = claim(db_session, started, entry=entry)
    dispose(db_session, started, first)
    second, created = claim(db_session, started, entry=entry)
    assert created is True and second.id != first.id and second.public_id != first.public_id
    assert _claim_count(db_session, started) == 2


def test_the_same_datum_for_a_different_signal_is_allowed(db_session) -> None:
    started = build_started(db_session, signal_names=("Signal A", "Signal B"))
    entry = make_entry(db_session, started.campaign)
    one, _ = claim(db_session, started, entry=entry, signal=started.signals[0])
    two, _ = claim(db_session, started, entry=entry, signal=started.signals[1])
    assert one.required_signal_id != two.required_signal_id and _claim_count(db_session, started) == 2


def test_several_data_for_one_signal_are_allowed(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    claim(db_session, started, entry=entry, metric_name="clicks")
    claim(db_session, started, entry=entry, metric_name="impressions")
    claim(db_session, started, entry=make_entry(db_session, started.campaign), metric_name="clicks")
    assert _claim_count(db_session, started) == 3


# --- structural validation / non-leaky tenancy ----------------------------------------------------------------------


def test_a_metric_name_absent_from_the_entry_is_refused_422_and_nothing_persists(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    with pytest.raises(EvidenceClaimMetricNotInEntryError):
        claim(db_session, started, entry=entry, metric_name="reach")
    with pytest.raises(EvidenceClaimMetricNotInEntryError):
        claim(db_session, started, entry=entry, metric_name="Clicks")  # exact match: no case folding
    with pytest.raises(EvidenceClaimMetricNotInEntryError):
        claim(db_session, started, entry=entry, metric_name=" clicks")  # exact match: no trimming
    assert _claim_count(db_session, started) == 0


def test_a_signal_of_another_contract_of_the_same_experiment_is_422(db_session) -> None:
    """Contract v1 -> authorize -> revoke (before any Start the freeze reopens) -> v2 -> authorize -> start v2.
    A v1 signal belongs to THIS Experiment but not to the Contract the started Authorization pins."""
    from tests.strategytest import build_current_experiment
    from tests.test_execution_authorization_domain import _declare_variant, _define

    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)
    _declare_variant(db_session, campaign, experiment, actor, version)
    contract_one = declare_contract(
        db_session, campaign, experiment, actor, version, signals=[_signal_payload("Old signal")], key="c-1"
    )
    first_authorization, _snap, _c = _authorize(db_session, campaign, experiment, actor, key="a-1")
    ExperimentExecutionAuthorizationService(db_session).revoke(
        campaign=campaign, experiment_public_id=experiment.public_id, reason="Redo.", actor_user_id=actor.id
    )
    declare_contract(
        db_session, campaign, experiment, actor, version, signals=[_signal_payload("New signal")], key="c-2",
        base_version=1,
    )
    second_authorization, _snap, _c = _authorize(db_session, campaign, experiment, actor, key="a-2")
    assert second_authorization.contract_version_id != contract_one.id
    _a, start, _created = start_authorization(db_session, campaign, experiment, actor, second_authorization)

    from app.strategy.repository import MeasurementContractRepository
    from tests.evidenceclaimtest import Started

    old_signal = MeasurementContractRepository(db_session).list_signals_for_version(contract_version_id=contract_one.id)[0]
    new_signal = MeasurementContractRepository(db_session).list_signals_for_version(
        contract_version_id=second_authorization.contract_version_id
    )[0]
    started = Started(campaign, experiment, actor, version, second_authorization, start, [new_signal])
    entry = make_entry(db_session, campaign)
    with pytest.raises(EvidenceClaimSignalNotInPinnedContractError):
        claim(db_session, started, entry=entry, signal=old_signal)
    row, _ = claim(db_session, started, entry=entry, signal=new_signal)  # the pinned contract's signal is fine
    assert row.contract_version_id == second_authorization.contract_version_id
    assert _claim_count(db_session, started) == 1


def test_a_signal_of_another_experiment_is_the_non_leaky_forbidden(db_session) -> None:
    started = build_started(db_session)
    other = build_started(db_session, campaign_name="Other Claim Campaign", within_workspace_id=started.campaign.workspace_id, suffix="-o")
    entry = make_entry(db_session, started.campaign)
    with pytest.raises(ForbiddenError):
        claim(db_session, started, entry=entry, signal=other.signal)
    assert _claim_count(db_session, started) == 0


def test_an_unknown_signal_entry_or_start_is_the_non_leaky_forbidden(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    service = ExperimentEvidenceClaimService(db_session)
    base = dict(
        campaign=started.campaign, experiment_public_id=started.experiment.public_id,
        start_public_id=started.start.public_id, client_request_id=uuid.uuid4().hex,
        required_signal_public_id=started.signal.public_id, metric_entry_public_id=entry.public_id,
        metric_name="clicks", actor_user_id=started.actor.id,
    )
    for override in (
        {"required_signal_public_id": "RSG-unknown0000"},
        {"metric_entry_public_id": "MET-unknown0000"},
        {"start_public_id": "EXS-unknown0000"},
        {"experiment_public_id": "EXP-unknown0000"},
    ):
        with pytest.raises(ForbiddenError):
            service.create(**{**base, **override})
    assert _claim_count(db_session, started) == 0


def test_a_start_of_another_experiment_is_the_non_leaky_forbidden(db_session) -> None:
    started = build_started(db_session)
    other = build_started(db_session, campaign_name="Other Claim Campaign", within_workspace_id=started.campaign.workspace_id, suffix="-o")
    entry = make_entry(db_session, started.campaign)
    with pytest.raises(ForbiddenError):
        claim(db_session, started, entry=entry, start=other.start)  # other Experiment's Start under THIS Experiment path
    assert _claim_count(db_session, started) == 0


def test_a_cross_campaign_entry_of_the_same_workspace_is_the_non_leaky_forbidden(db_session) -> None:
    started = build_started(db_session)
    other = build_started(db_session, campaign_name="Other Claim Campaign", within_workspace_id=started.campaign.workspace_id, suffix="-o")
    foreign_entry = make_entry(db_session, other.campaign)
    assert foreign_entry.workspace_id == started.campaign.workspace_id and foreign_entry.campaign_id != started.campaign.id
    with pytest.raises(ForbiddenError):
        claim(db_session, started, entry=foreign_entry)
    assert _claim_count(db_session, started) == 0


def test_a_cross_workspace_entry_is_the_non_leaky_forbidden(db_session) -> None:
    started = build_started(db_session)
    other = build_started(db_session, campaign_name="Other Tenant")
    assert other.campaign.workspace_id != started.campaign.workspace_id
    foreign_entry = make_entry(db_session, other.campaign)
    with pytest.raises(ForbiddenError):
        claim(db_session, started, entry=foreign_entry)
    assert _claim_count(db_session, started) == 0


# --- revocation (R2) and reauthorization ------------------------------------------------------------------------


def test_a_late_claim_after_revocation_is_allowed_and_revoked_at_is_exposed_literally(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    revoke(db_session, started, reason="Paused.")
    row, created = claim(db_session, started, entry=entry)  # R2: no active Authorization required
    assert created is True
    view = _view(db_session, started, row)
    assert view.authorization.revoked_at is not None and view.authorization.revoked_reason == "Paused."
    assert view.authorization.id == started.authorization.public_id


def test_claims_under_two_starts_never_migrate_between_attempts(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    under_a, _ = claim(db_session, started, entry=entry)
    revoke(db_session, started)
    second_authorization, _snap, _c = _authorize(
        db_session, started.campaign, started.experiment, started.actor, key="a-2"
    )
    _a, second_start, _created = start_authorization(
        db_session, started.campaign, started.experiment, started.actor, second_authorization, key="s-2"
    )
    # Same Experiment, same Contract, same datum, same signal — a DIFFERENT attempt.
    under_b, created = claim(db_session, started, entry=entry, start=second_start)
    assert created is True and under_b.id != under_a.id
    db_session.refresh(under_a)
    assert under_a.start_id == started.start.id and under_a.authorization_id == started.authorization.id
    assert under_b.start_id == second_start.id and under_b.authorization_id == second_authorization.id
    assert under_a.contract_version_id == under_b.contract_version_id
    listing_a = ExperimentEvidenceClaimService(db_session).list_for_start(
        campaign=started.campaign, experiment_public_id=started.experiment.public_id,
        start_public_id=started.start.public_id,
    )[1]
    listing_b = ExperimentEvidenceClaimService(db_session).list_for_start(
        campaign=started.campaign, experiment_public_id=started.experiment.public_id,
        start_public_id=second_start.public_id,
    )[1]
    assert [v.claim.id for v in listing_a] == [under_a.id] and [v.claim.id for v in listing_b] == [under_b.id]


def test_revocation_and_reauthorization_never_mutate_existing_claims(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    row, _ = claim(db_session, started, entry=entry)
    before = (row.start_id, row.authorization_id, row.disposed_at, row.disposal_reason)
    revoke(db_session, started)
    _authorize(db_session, started.campaign, started.experiment, started.actor, key="a-2")
    db_session.refresh(row)
    assert (row.start_id, row.authorization_id, row.disposed_at, row.disposal_reason) == before


# --- temporal (T1), tracking, Variant firewalls -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "period",
    [
        (date(2000, 1, 1), date(2000, 1, 31)),  # entirely before the start
        (date(2020, 1, 1), date(2099, 1, 1)),  # overlapping / straddling the start
        (date(2098, 1, 1), date(2098, 12, 31)),  # entirely in the future
    ],
)
def test_no_temporal_validation_any_period_commits(db_session, period) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign, period_start=period[0], period_end=period[1])
    row, created = claim(db_session, started, entry=entry)
    assert created is True
    view = _view(db_session, started, row)
    # The literal facts are shown side by side; no eligibility conclusion is produced.
    assert (view.datum.period_start, view.datum.period_end) == period
    assert view.start.started_at == started.start.started_at
    assert not {"eligible", "eligibility", "temporal_status", "before_start", "after_start"} & set(EvidenceClaimPublic.model_fields)


def test_tracking_required_never_blocks_creation_and_creates_no_tracking_object(db_session) -> None:
    started = build_started(db_session, tracking_required=True)
    assert started.signal.tracking_required is True
    entry = make_entry(db_session, started.campaign)
    before = _table_counts(db_session)
    row, created = claim(db_session, started, entry=entry)
    after = _table_counts(db_session)
    assert created is True
    assert {k: after[k] - before[k] for k in after if after[k] != before[k]} == {
        "experiment_evidence_claims": 1, "audit_events": 1,
    }  # no TrackingPlan/TrackingRequirement/validation row of any kind
    assert _view(db_session, started, row).required_signal.tracking_required is True


def test_the_claim_surface_has_no_variant_assignment_or_exposure_input() -> None:
    for model in (CreateEvidenceClaimRequest, DisposeEvidenceClaimRequest):
        assert not {"variant_id", "assignment_id", "exposure_id", "note", "eligible", "valid", "result", "winner"} & set(
            model.model_fields
        )
    with pytest.raises(ValidationError):
        CreateEvidenceClaimRequest(
            client_request_id="k", required_signal_id="RSG-x", metric_entry_id="MET-x", metric_name="clicks",
            variant_id="VAR-x",
        )
    with pytest.raises(ValidationError):
        CreateEvidenceClaimRequest(
            client_request_id="k", required_signal_id="RSG-x", metric_entry_id="MET-x", metric_name="clicks", note="n"
        )


# --- corrections (C1) --------------------------------------------------------------------------------------------------


def test_an_old_claim_stays_historical_and_a_corrected_datum_needs_a_new_claim(db_session) -> None:
    started = build_started(db_session)
    original = make_entry(db_session, started.campaign, created_at=utc(2026, 2, 1))
    old_claim, _ = claim(db_session, started, entry=original)
    assert _view(db_session, started, old_claim).later_correction_exists is False

    correction = make_entry(  # same (campaign, period, channel) grouping, later => the current row
        db_session, started.campaign, values={"clicks": Decimal("75")}, created_at=utc(2026, 2, 2)
    )
    db_session.refresh(old_claim)
    assert old_claim.metric_entry_id == original.id  # never retargeted
    assert _claim_count(db_session, started) == 1  # the correction created no claim
    assert _view(db_session, started, old_claim).later_correction_exists is True  # observation only

    new_claim, created = claim(db_session, started, entry=correction)  # the corrected datum needs its OWN claim
    assert created is True and new_claim.metric_entry_id == correction.id
    assert _view(db_session, started, new_claim).later_correction_exists is False


def test_a_claim_on_an_already_non_current_datum_is_allowed_and_flagged(db_session) -> None:
    started = build_started(db_session)
    original = make_entry(db_session, started.campaign, created_at=utc(2026, 2, 1))
    make_entry(db_session, started.campaign, created_at=utc(2026, 2, 2))  # supersedes `original` in its grouping
    row, created = claim(db_session, started, entry=original)  # C1: allowed, explicitly historical
    assert created is True
    assert _view(db_session, started, row).later_correction_exists is True


def test_a_different_grouping_is_not_a_correction(db_session) -> None:
    started = build_started(db_session)
    original = make_entry(db_session, started.campaign, created_at=utc(2026, 2, 1), channel="email")
    make_entry(db_session, started.campaign, created_at=utc(2026, 2, 2), channel="social")
    row, _ = claim(db_session, started, entry=original)
    assert _view(db_session, started, row).later_correction_exists is False


# --- distribution-owned entries (D1) -----------------------------------------------------------------------------------


def test_distribution_owned_entries_are_claimable_and_disclosed(db_session) -> None:
    started = build_started(db_session)
    distribution, user = build_distribution_in_campaign(db_session, started)
    evidence, entry = make_distribution_evidence(db_session, started, distribution, user)
    row, created = claim(db_session, started, entry=entry, metric_name="reach")
    assert created is True
    view = _view(db_session, started, row)
    assert view.excluded_from_aggregate_and_analysis is True
    assert view.distribution.evidence_id == evidence.public_id
    assert view.distribution.distribution_id == distribution.public_id
    assert view.distribution.source_reference == "ref"
    assert view.distribution.supersedes_evidence_id is None and view.distribution.superseded_by_evidence_id is None
    assert view.later_correction_exists is False
    assert view.datum.value == Decimal("500.0000")


def test_a_distribution_evidence_correction_is_observed_but_never_retargets(db_session) -> None:
    started = build_started(db_session)
    distribution, user = build_distribution_in_campaign(db_session, started)
    evidence, entry = make_distribution_evidence(db_session, started, distribution, user)
    old_claim, _ = claim(db_session, started, entry=entry, metric_name="reach")
    corrected_evidence, corrected_entry = correct_distribution_evidence(
        db_session, started, distribution, user, evidence
    )
    db_session.refresh(old_claim)
    assert old_claim.metric_entry_id == entry.id  # never retargeted
    view = _view(db_session, started, old_claim)
    assert view.later_correction_exists is True
    assert view.distribution.superseded_by_evidence_id == corrected_evidence.public_id
    new_claim, created = claim(db_session, started, entry=corrected_entry, metric_name="reach")
    assert created is True
    new_view = _view(db_session, started, new_claim)
    assert new_view.later_correction_exists is False
    assert new_view.distribution.supersedes_evidence_id == evidence.public_id


def test_an_aggregate_entry_has_no_distribution_context(db_session) -> None:
    started = build_started(db_session)
    row, _ = claim(db_session, started, entry=make_entry(db_session, started.campaign))
    view = _view(db_session, started, row)
    assert view.distribution is None and view.excluded_from_aggregate_and_analysis is False


# --- disposal -------------------------------------------------------------------------------------------------------------


def test_dispose_is_a_one_way_atomic_triple_and_the_claim_stays_readable(db_session) -> None:
    started = build_started(db_session)
    row, _ = claim(db_session, started, entry=make_entry(db_session, started.campaign))
    disposer = make_user(db_session)  # a DIFFERENT member may dispose another member's claim
    disposed = dispose(db_session, started, row, reason="Wrong signal.", actor=disposer)
    assert disposed.disposed_at is not None and disposed.disposed_by_user_id == disposer.id
    assert disposed.disposal_reason == "Wrong signal."
    assert disposed.claimed_by_user_id == started.actor.id  # the original assertion is never rewritten
    events = _events(db_session, started, EVENT_EVIDENCE_CLAIM_DISPOSED)
    assert len(events) == 1
    assert events[0].actor_type is ActorType.USER and events[0].actor_user_id == disposer.id
    assert (events[0].previous_state, events[0].new_state) == (f"claimed:{row.public_id}", f"disposed:{row.public_id}")
    assert events[0].experiment_evidence_claim_id == row.id
    assert events[0].strategy_id is None and events[0].hypothesis_id is None and events[0].metric_entry_id is None
    listing = ExperimentEvidenceClaimService(db_session).list_for_start(
        campaign=started.campaign, experiment_public_id=started.experiment.public_id,
        start_public_id=started.start.public_id,
    )[1]
    assert [v.claim.id for v in listing] == [row.id]  # disposed claims stay readable
    view = _view(db_session, started, row)
    assert view.is_disposed is True and view.disposed_by == disposer.public_id and view.disposal_reason == "Wrong signal."


def test_a_second_dispose_is_already_disposed_and_nothing_changes(db_session) -> None:
    started = build_started(db_session)
    row, _ = claim(db_session, started, entry=make_entry(db_session, started.campaign))
    dispose(db_session, started, row, reason="First.")
    first_at = row.disposed_at
    with pytest.raises(EvidenceClaimAlreadyDisposedError):
        dispose(db_session, started, row, reason="Second.")
    db_session.refresh(row)
    assert row.disposal_reason == "First." and row.disposed_at == first_at  # no reactivation, no rewrite
    assert len(_events(db_session, started, EVENT_EVIDENCE_CLAIM_DISPOSED)) == 1


def test_dispose_of_an_unknown_or_foreign_claim_is_the_non_leaky_forbidden(db_session) -> None:
    started = build_started(db_session)
    other = build_started(db_session, campaign_name="Other Claim Campaign", within_workspace_id=started.campaign.workspace_id, suffix="-o")
    foreign, _ = claim(db_session, other, entry=make_entry(db_session, other.campaign))
    service = ExperimentEvidenceClaimService(db_session)
    for claim_public_id in ("ECL-unknown0000", foreign.public_id):  # unknown / another Start's claim
        with pytest.raises(ForbiddenError):
            service.dispose(
                campaign=started.campaign, experiment_public_id=started.experiment.public_id,
                start_public_id=started.start.public_id, claim_public_id=claim_public_id,
                reason="x", actor_user_id=started.actor.id,
            )
    db_session.refresh(foreign)
    assert foreign.disposed_at is None


def test_the_dispose_request_requires_a_nonblank_reason() -> None:
    for bad in ("", "   ", "x" * 1001, "a\x00b"):
        with pytest.raises(ValidationError):
            DisposeEvidenceClaimRequest(reason=bad)
    with pytest.raises(ValidationError):
        DisposeEvidenceClaimRequest()
    with pytest.raises(ValidationError):
        DisposeEvidenceClaimRequest(reason="ok", client_request_id="k")  # dispose carries no idempotency key
    assert DisposeEvidenceClaimRequest(reason="  padded  ").reason == "padded"


# --- read model / semantic firewall ------------------------------------------------------------------------------------


def test_the_read_model_carries_the_frozen_constants_and_literal_facts(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    row, _ = claim(db_session, started, entry=entry)
    view = _view(db_session, started, row)
    assert view.semantics == EVIDENCE_CLAIM_SEMANTICS == "PROVENANCE CLAIM ONLY — NOT ELIGIBILITY OR VALIDATION"
    assert view.scope == "EXPERIMENT_LEVEL"
    assert "not necessarily the member who originally reported" in view.reporter_note
    assert view.id == row.public_id and view.experiment_id == started.experiment.public_id
    assert view.claimed_by is not None and view.claimed_by.startswith("USR-")
    assert view.is_disposed is False and view.disposed_at is None and view.disposed_by is None
    assert view.authorization.id == started.authorization.public_id and view.authorization.revoked_at is None
    assert view.start.id == started.start.public_id and view.start.started_at == started.start.started_at
    assert view.required_signal.id == started.signal.public_id and view.required_signal.name == started.signal.name
    assert view.required_signal.contract_version == 1 and view.required_signal.tracking_required is False
    assert view.datum.metric_entry_id == entry.public_id and view.datum.metric_name == "clicks"
    assert view.datum.value == Decimal("50.0000") and view.datum.channel == "email"
    assert (view.datum.period_start, view.datum.period_end) == (entry.period_start, entry.period_end)
    assert view.datum.source.value == "MANUAL" and view.datum.entry_created_at == entry.created_at
    assert view.distribution is None


def test_the_read_model_exposes_no_eligibility_or_validity_semantics() -> None:
    """Scoped SEMANTIC assertions on the claim's own schemas — not a repository-wide lexical ban."""
    forbidden_fields = {
        "eligible", "eligibility", "accepted", "verified", "validated", "qualified", "valid", "validity", "is_valid",
        "status", "current", "is_current", "variant", "variant_id", "assignment", "assignment_id", "exposure",
        "exposure_id", "result", "winner", "verdict", "attribution", "causal", "causality", "sufficient",
        "sufficiency", "comparable", "tracking_valid", "temporal_eligibility",
    }
    from app.strategy import schemas

    for model in (
        EvidenceClaimPublic, schemas.EvidenceClaimDatum, schemas.EvidenceClaimSignalRef, schemas.EvidenceClaimStartRef,
        schemas.EvidenceClaimAuthorizationRef, schemas.EvidenceClaimDistributionContext, schemas.EvidenceClaimListResponse,
    ):
        assert not forbidden_fields & set(model.model_fields), model.__name__
    assert EVIDENCE_CLAIM_SEMANTICS.startswith("PROVENANCE CLAIM ONLY") and "NOT ELIGIBILITY" in EVIDENCE_CLAIM_SEMANTICS
    # The one correction observation is named as an observation, never as a status.
    assert "later_correction_exists" in EvidenceClaimPublic.model_fields


def test_the_writer_surface_is_append_only_with_a_single_one_way_disposal() -> None:
    public = {name for name in dir(ExperimentEvidenceClaimRepository) if not name.startswith("_")}
    assert public == {
        "create", "dispose", "get_by_workspace_and_request_id", "get_active_for_material",
        "get_for_start_by_public_id", "list_for_start",
    }
    assert not {"update", "delete", "retarget", "reactivate", "undispose", "restore"} & public


def test_the_service_never_accepts_a_system_or_agent_actor() -> None:
    import inspect as _inspect

    create_params = _inspect.signature(ExperimentEvidenceClaimService.create).parameters
    dispose_params = _inspect.signature(ExperimentEvidenceClaimService.dispose).parameters
    for params in (create_params, dispose_params):
        assert params["actor_user_id"].default is _inspect.Parameter.empty  # a real USER is required
        assert not {"actor_type", "system", "agent"} & set(params)


# --- non-effects ------------------------------------------------------------------------------------------------------------


def test_claim_and_disposal_have_no_side_effects_beyond_the_claim_and_audit_rows(db_session) -> None:
    started = build_started(db_session)
    entry = make_entry(db_session, started.campaign)
    hypothesis = db_session.get(Hypothesis, started.experiment.hypothesis_id)
    snapshot = (
        hypothesis.status, started.experiment.status, started.experiment.description,
        started.authorization.revoked_at, started.authorization.superseded_by_execution_authorization_id,
        started.start.started_at, started.start.authorization_id,
    )
    before = _table_counts(db_session)
    row, _ = claim(db_session, started, entry=entry)
    after_create = _table_counts(db_session)
    dispose(db_session, started, row)
    after_dispose = _table_counts(db_session)

    def delta(a, b):
        return {k: b[k] - a[k] for k in b if b[k] != a[k]}

    # Nothing else moved: no Measurement run, Observation, Signal, AnalysisResult, Learning, Tracking, Commercial row.
    assert delta(before, after_create) == {"experiment_evidence_claims": 1, "audit_events": 1}
    assert delta(after_create, after_dispose) == {"audit_events": 1}
    for obj in (hypothesis, started.experiment, started.authorization, started.start):
        db_session.refresh(obj)
    assert snapshot == (
        hypothesis.status, started.experiment.status, started.experiment.description,
        started.authorization.revoked_at, started.authorization.superseded_by_execution_authorization_id,
        started.start.started_at, started.start.authorization_id,
    )
