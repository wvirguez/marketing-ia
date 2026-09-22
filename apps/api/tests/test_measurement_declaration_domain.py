"""Service-level tests for the Pre-Execution Measurement Declaration (Model A + D,
no new aggregate): structured DESCRIPTIVE / COMPARATIVE creation, the all-or-nothing
completeness rules, the CONTROLLED firewall, ANY-vs-EXACT ownership in both insertion
orders and during revision, material equality / idempotency / unchanged detection, the
inherited C1 freeze, and Evidence Claim structural compatibility. All marked
`postgres` and driven through the real production services.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.audit.models import AuditEvent
from app.core.api_errors import (
    EvidenceClaimChannelNotBoundError,
    EvidenceClaimMetricNotBoundError,
    IdempotencyKeyConflictError,
    MeasurementContractBindingConflictError,
    MeasurementContractControlledDeclarationNotSupportedError,
    MeasurementContractDeclarationInvalidError,
    MeasurementContractFrozenByAuthorizationError,
    MeasurementContractFrozenByExecutionStartError,
    MeasurementContractUnchangedError,
)
from app.strategy.measurement_contract_service import ExperimentMeasurementContractService
from app.strategy.models import MeasurementContractVersion
from tests.declarationtest import (
    bound_signal,
    build_experiment_with_definition,
    build_structured_started,
    comparative_signals,
    declare,
    descriptive_signals,
    legacy_signal,
)
from tests.evidenceclaimtest import build_started, claim, make_entry
from tests.test_execution_authorization_domain import _authorize, _declare_variant
from tests.evidenceclaimtest import revoke, start_authorization

pytestmark = pytest.mark.postgres


# --- legacy compatibility ---------------------------------------------------------------------


def test_legacy_declaration_is_unchanged_and_all_null(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    row, signals, _definition, created = declare(
        db_session, campaign, experiment, actor, version, level=None, window=None, signals=[legacy_signal()]
    )
    assert created is True
    assert (row.declaration_level, row.declaration_semantics_version, row.baseline_window_days) == (None, None, None)
    assert all(
        (s.bound_metric_name, s.channel_binding, s.bound_channel, s.min_data_points) == (None, None, None, None)
        for s in signals
    )


def test_a_legacy_revision_keeps_the_pre_declaration_bounds(db_session) -> None:
    """The structured 4..3650 bounds are semantics-v1 rules: a LEGACY window of 1 day stays legal."""
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    declare(db_session, campaign, experiment, actor, version, level=None, window=1, signals=[legacy_signal()])
    row, _signals, _definition, created = declare(
        db_session, campaign, experiment, actor, version, level=None, window=2, base_version=1, key="c-2",
        signals=[legacy_signal()],
    )
    assert created is True and row.version == 2 and row.measurement_window_days == 2


# --- structured creation ------------------------------------------------------------------------


def test_structured_descriptive_creation_persists_and_reads_back(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    row, signals, _definition, created = declare(
        db_session, campaign, experiment, actor, version, level="DESCRIPTIVE", window=14,
        signals=[bound_signal(), bound_signal("Reach", metric="reach", binding="EXACT", channel="email", min_points=3)],
    )
    assert created is True
    assert (row.declaration_level, row.declaration_semantics_version, row.baseline_window_days) == ("DESCRIPTIVE", 1, None)
    assert row.measurement_window_days == 14
    assert [(s.bound_metric_name, s.channel_binding, s.bound_channel, s.min_data_points) for s in signals] == [
        ("clicks", "ANY", None, 1),
        ("reach", "EXACT", "email", 3),
    ]
    service = ExperimentMeasurementContractService(db_session)
    _experiment, history = service.get_history(campaign=campaign, experiment_public_id=experiment.public_id)
    assert [v.declaration_level for v, _s in history] == ["DESCRIPTIVE"]
    assert [s.bound_channel for s in history[0][1]] == [None, "email"]


def test_structured_comparative_creation_requires_a_baseline_and_no_min_data_points(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    row, signals, _definition, created = declare(
        db_session, campaign, experiment, actor, version, level="COMPARATIVE", window=14, baseline=7,
        signals=comparative_signals(),
    )
    assert created is True
    assert (row.declaration_level, row.baseline_window_days) == ("COMPARATIVE", 7)
    assert signals[0].min_data_points is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"window": None},
        {"window": 3},
        {"window": 3651},
        {"level": "DESCRIPTIVE", "baseline": 7},  # baseline forbidden for DESCRIPTIVE
        {"semantics_version": 2},
        {"semantics_version": 0},
        {"semantics_version": None},
        {"level": "STRUCTURED"},
    ],
)
def test_malformed_structured_declarations_are_rejected_and_write_nothing(db_session, overrides) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    kwargs = {"level": "DESCRIPTIVE", "window": 14, "signals": descriptive_signals()}
    kwargs.update(overrides)
    semantics_version = kwargs.pop("semantics_version", 1)
    level = kwargs.pop("level")
    with pytest.raises(MeasurementContractDeclarationInvalidError):
        ExperimentMeasurementContractService(db_session).declare_or_revise(
            campaign=campaign, experiment_public_id=experiment.public_id, base_version=0, client_request_id="c-1",
            definition_version_public_id=version.public_id, measurement_window_days=kwargs["window"],
            minimum_evidence=None, success_criterion=None, analysis_method_intent=None, stopping_rule=None,
            decision_rule_intent=None, declaration_level=level, declaration_semantics_version=semantics_version,
            baseline_window_days=kwargs.get("baseline"), signals=kwargs["signals"], actor_user_id=actor.id,
        )
    assert db_session.scalar(select(MeasurementContractVersion.id).where(MeasurementContractVersion.experiment_id == experiment.id)) is None


def test_window_and_baseline_bounds_are_inclusive(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    declare(db_session, campaign, experiment, actor, version, level="COMPARATIVE", window=4, baseline=4, signals=comparative_signals())
    row, _s, _d, created = declare(
        db_session, campaign, experiment, actor, version, level="COMPARATIVE", window=3650, baseline=3650,
        signals=comparative_signals(), base_version=1, key="c-2",
    )
    assert created is True and (row.measurement_window_days, row.baseline_window_days) == (3650, 3650)
    with pytest.raises(MeasurementContractDeclarationInvalidError):
        declare(db_session, campaign, experiment, actor, version, level="COMPARATIVE", window=14, baseline=3, signals=comparative_signals(), base_version=2, key="c-3")
    with pytest.raises(MeasurementContractDeclarationInvalidError):
        declare(db_session, campaign, experiment, actor, version, level="COMPARATIVE", window=14, baseline=3651, signals=comparative_signals(), base_version=2, key="c-4")
    with pytest.raises(MeasurementContractDeclarationInvalidError):
        declare(db_session, campaign, experiment, actor, version, level="COMPARATIVE", window=14, baseline=None, signals=comparative_signals(), base_version=2, key="c-5")


@pytest.mark.parametrize(
    "level,signal",
    [
        ("DESCRIPTIVE", bound_signal(min_points=None)),  # min_data_points required
        ("DESCRIPTIVE", bound_signal(min_points=0)),
        ("COMPARATIVE", bound_signal(min_points=2)),  # min_data_points forbidden
        ("DESCRIPTIVE", bound_signal(metric=None)),  # partially bound
        ("DESCRIPTIVE", bound_signal(binding=None)),
        ("DESCRIPTIVE", bound_signal(binding="ANY", channel="email")),  # ANY forbids a channel
        ("DESCRIPTIVE", bound_signal(binding="EXACT", channel=None)),  # EXACT requires one
        ("DESCRIPTIVE", bound_signal(binding="SOME")),
    ],
)
def test_incomplete_or_inconsistent_signals_are_rejected(db_session, level, signal) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    with pytest.raises(MeasurementContractDeclarationInvalidError):
        declare(db_session, campaign, experiment, actor, version, level=level, baseline=7 if level == "COMPARATIVE" else None, signals=[signal])


def test_a_structured_declaration_needs_every_signal_bound(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    with pytest.raises(MeasurementContractDeclarationInvalidError):
        declare(db_session, campaign, experiment, actor, version, level="DESCRIPTIVE", signals=[bound_signal(), legacy_signal("Other")])


def test_a_legacy_declaration_may_not_carry_any_structured_field(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    with pytest.raises(MeasurementContractDeclarationInvalidError):
        declare(db_session, campaign, experiment, actor, version, level=None, window=None, signals=[bound_signal()])
    with pytest.raises(MeasurementContractDeclarationInvalidError):
        declare(db_session, campaign, experiment, actor, version, level=None, window=None, baseline=7, signals=[legacy_signal()])
    with pytest.raises(MeasurementContractDeclarationInvalidError):  # version without level
        ExperimentMeasurementContractService(db_session).declare_or_revise(
            campaign=campaign, experiment_public_id=experiment.public_id, base_version=0, client_request_id="c-9",
            definition_version_public_id=version.public_id, measurement_window_days=None, minimum_evidence=None,
            success_criterion=None, analysis_method_intent=None, stopping_rule=None, decision_rule_intent=None,
            declaration_level=None, declaration_semantics_version=1, baseline_window_days=None,
            signals=[legacy_signal()], actor_user_id=actor.id,
        )


def test_binding_strings_are_trimmed_before_persistence_and_comparison(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    row, signals, _d, _c = declare(
        db_session, campaign, experiment, actor, version,
        signals=[bound_signal(metric="  clicks  ", binding="EXACT", channel="  email\t")],
    )
    assert (signals[0].bound_metric_name, signals[0].bound_channel) == ("clicks", "email")
    # A trimmed equivalent is the SAME request (replay), and the case-different one is not.
    _r, _s, _d2, created = declare(
        db_session, campaign, experiment, actor, version, signals=[bound_signal(metric="clicks", binding="EXACT", channel="email")]
    )
    assert created is False
    with pytest.raises(IdempotencyKeyConflictError):
        declare(db_session, campaign, experiment, actor, version, signals=[bound_signal(metric="Clicks", binding="EXACT", channel="email")])


# --- CONTROLLED firewall ------------------------------------------------------------------------


def test_a_structured_declaration_on_a_controlled_definition_is_rejected(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(
        db_session, comparison_type="CONTROLLED", controlled_factors=["Format"]
    )
    for level, kwargs in (("DESCRIPTIVE", {}), ("COMPARATIVE", {"baseline": 7})):
        with pytest.raises(MeasurementContractControlledDeclarationNotSupportedError):
            declare(
                db_session, campaign, experiment, actor, version, level=level, success_criterion="CTR improves.",
                signals=descriptive_signals() if level == "DESCRIPTIVE" else comparative_signals(), **kwargs,
            )
    # ...and a LEGACY CONTROLLED declaration keeps working exactly as before.
    row, _s, _d, created = declare(
        db_session, campaign, experiment, actor, version, level=None, window=None, success_criterion="CTR improves.",
        signals=[legacy_signal()],
    )
    assert created is True and row.declaration_level is None


# --- ANY vs EXACT ownership ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "signals",
    [
        [bound_signal("A", binding="ANY"), bound_signal("B", binding="EXACT", channel="email")],  # ANY then EXACT
        [bound_signal("A", binding="EXACT", channel="email"), bound_signal("B", binding="ANY")],  # EXACT then ANY
        [bound_signal("A", binding="EXACT", channel="email"), bound_signal("B", binding="EXACT", channel="sms"), bound_signal("C", binding="ANY")],
    ],
)
def test_any_and_exact_for_one_metric_are_rejected_in_both_orders(db_session, signals) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    with pytest.raises(MeasurementContractBindingConflictError):
        declare(db_session, campaign, experiment, actor, version, signals=signals)


def test_the_ownership_rule_also_holds_during_revision(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    declare(db_session, campaign, experiment, actor, version, signals=[bound_signal("A", binding="ANY")])
    with pytest.raises(MeasurementContractBindingConflictError):
        declare(
            db_session, campaign, experiment, actor, version, base_version=1, key="c-2",
            signals=[bound_signal("A", binding="ANY"), bound_signal("B", binding="EXACT", channel="email")],
        )
    with pytest.raises(MeasurementContractBindingConflictError):  # a duplicate slot is a typed rejection, never a 500
        declare(
            db_session, campaign, experiment, actor, version, base_version=1, key="c-3",
            signals=[bound_signal("A", binding="ANY"), bound_signal("B", binding="ANY")],
        )


def test_distinct_exact_channels_and_distinct_metrics_are_allowed(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    _r, signals, _d, created = declare(
        db_session, campaign, experiment, actor, version,
        signals=[
            bound_signal("A", binding="EXACT", channel="email"),
            bound_signal("B", binding="EXACT", channel="sms"),
            bound_signal("C", metric="reach", binding="ANY"),
        ],
    )
    assert created is True and len(signals) == 3


def test_exact_channels_differing_only_by_case_are_distinct(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    _r, signals, _d, created = declare(
        db_session, campaign, experiment, actor, version,
        signals=[bound_signal("A", binding="EXACT", channel="email"), bound_signal("B", binding="EXACT", channel="Email")],
    )
    assert created is True and len(signals) == 2


# --- material equality / idempotency / unchanged ---------------------------------------------------


def test_same_key_and_same_full_semantics_replays_the_original(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    first, _s, _d, created = declare(db_session, campaign, experiment, actor, version, signals=descriptive_signals())
    replay, _s2, _d2, replay_created = declare(db_session, campaign, experiment, actor, version, signals=descriptive_signals())
    assert created is True and replay_created is False and replay.id == first.id


@pytest.mark.parametrize(
    "changed",
    [
        {"level": "COMPARATIVE", "baseline": 7, "signals": comparative_signals()},
        {"window": 15},
        {"signals": [bound_signal(metric="reach")]},
        {"signals": [bound_signal(binding="EXACT", channel="email")]},
        {"signals": [bound_signal(min_points=2)]},
    ],
)
def test_same_key_and_changed_structured_semantics_is_an_idempotency_conflict(db_session, changed) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    declare(db_session, campaign, experiment, actor, version, signals=descriptive_signals())
    kwargs = {"level": "DESCRIPTIVE", "signals": descriptive_signals()}
    kwargs.update(changed)
    with pytest.raises(IdempotencyKeyConflictError):
        declare(db_session, campaign, experiment, actor, version, **kwargs)


def test_legacy_to_structured_replay_is_a_conflict_and_a_revision_is_material(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    declare(db_session, campaign, experiment, actor, version, level=None, window=14, signals=[legacy_signal()])
    with pytest.raises(IdempotencyKeyConflictError):  # same key, now structured
        declare(db_session, campaign, experiment, actor, version, signals=[bound_signal(min_points=1)])
    # LEGACY -> STRUCTURED with the same prose/window/signal name is NOT "unchanged": it is a new version.
    row, _s, _d, created = declare(
        db_session, campaign, experiment, actor, version, base_version=1, key="c-2", signals=[bound_signal()]
    )
    assert created is True and row.version == 2 and row.declaration_level == "DESCRIPTIVE"


def test_a_new_key_with_unchanged_structured_semantics_is_unchanged(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    declare(db_session, campaign, experiment, actor, version, signals=descriptive_signals())
    with pytest.raises(MeasurementContractUnchangedError):
        declare(db_session, campaign, experiment, actor, version, base_version=1, key="c-2", signals=descriptive_signals())


@pytest.mark.parametrize(
    "changed",
    [
        {"level": "COMPARATIVE", "baseline": 7, "signals": comparative_signals()},
        {"window": 20},
        {"signals": [bound_signal(metric="reach")]},
        {"signals": [bound_signal(binding="EXACT", channel="email")]},
        {"signals": [bound_signal(min_points=5)]},
    ],
)
def test_a_new_key_with_changed_structured_semantics_appends_a_version(db_session, changed) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    declare(db_session, campaign, experiment, actor, version, signals=descriptive_signals())
    kwargs = {"level": "DESCRIPTIVE", "signals": descriptive_signals()}
    kwargs.update(changed)
    row, _s, _d, created = declare(db_session, campaign, experiment, actor, version, base_version=1, key="c-2", **kwargs)
    assert created is True and row.version == 2


def test_baseline_change_alone_is_material(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    declare(db_session, campaign, experiment, actor, version, level="COMPARATIVE", baseline=7, signals=comparative_signals())
    row, _s, _d, created = declare(
        db_session, campaign, experiment, actor, version, level="COMPARATIVE", baseline=8, signals=comparative_signals(),
        base_version=1, key="c-2",
    )
    assert created is True and row.baseline_window_days == 8


def test_history_reconstructs_every_structured_version(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    declare(db_session, campaign, experiment, actor, version, level=None, window=None, signals=[legacy_signal()])
    declare(db_session, campaign, experiment, actor, version, base_version=1, key="c-2", signals=descriptive_signals())
    declare(
        db_session, campaign, experiment, actor, version, base_version=2, key="c-3", level="COMPARATIVE", baseline=9,
        signals=comparative_signals(),
    )
    _e, history = ExperimentMeasurementContractService(db_session).get_history(
        campaign=campaign, experiment_public_id=experiment.public_id
    )
    assert [(v.version, v.declaration_level, v.baseline_window_days) for v, _s in history] == [
        (1, None, None), (2, "DESCRIPTIVE", None), (3, "COMPARATIVE", 9),
    ]


def test_declaration_adds_no_audit_event_type_only_the_existing_lifecycle(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    declare(db_session, campaign, experiment, actor, version, signals=descriptive_signals())
    declare(db_session, campaign, experiment, actor, version, base_version=1, key="c-2", level="COMPARATIVE", baseline=7, signals=comparative_signals())
    types = list(
        db_session.scalars(
            select(AuditEvent.event_type).where(AuditEvent.experiment_id == experiment.id).order_by(AuditEvent.created_at, AuditEvent.id)
        )
    )
    contract_events = [t for t in types if "contract" in t or "declaration" in t]
    assert set(contract_events) == {"strategy.measurement_contract.declared", "strategy.measurement_contract.revised"}
    assert not [t for t in types if "declaration" in t]


# --- C1 freeze inheritance ------------------------------------------------------------------------


def test_an_active_authorization_blocks_a_structured_revision(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    _declare_variant(db_session, campaign, experiment, actor, version)
    declare(db_session, campaign, experiment, actor, version, signals=descriptive_signals())
    authorization, _snapshot, _created = _authorize(db_session, campaign, experiment, actor)
    assert authorization.contract_version_id is not None
    with pytest.raises(MeasurementContractFrozenByAuthorizationError):
        declare(
            db_session, campaign, experiment, actor, version, base_version=1, key="c-2", level="COMPARATIVE", baseline=7,
            signals=comparative_signals(),
        )


def test_any_start_freezes_a_structured_declaration_permanently_even_after_revocation(db_session) -> None:
    started = build_structured_started(db_session)
    with pytest.raises(MeasurementContractFrozenByExecutionStartError):  # the Start check precedes the active-Authorization one
        declare(
            db_session, started.campaign, started.experiment, started.actor, started.version, base_version=1,
            key="c-2", signals=[bound_signal(metric="reach")],
        )
    revoke(db_session, started)  # revocation after a Start never reopens the Contract lineage
    with pytest.raises(MeasurementContractFrozenByExecutionStartError):
        declare(
            db_session, started.campaign, started.experiment, started.actor, started.version, base_version=1,
            key="c-3", signals=[bound_signal(metric="reach")],
        )


def test_before_any_start_a_revoked_authorization_reopens_the_declaration(db_session) -> None:
    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    _declare_variant(db_session, campaign, experiment, actor, version)
    declare(db_session, campaign, experiment, actor, version, signals=descriptive_signals())
    _authorize(db_session, campaign, experiment, actor)
    from app.strategy.execution_authorization_service import ExperimentExecutionAuthorizationService

    ExperimentExecutionAuthorizationService(db_session).revoke(
        campaign=campaign, experiment_public_id=experiment.public_id, reason="Rethinking.", actor_user_id=actor.id
    )
    row, _s, _d, created = declare(
        db_session, campaign, experiment, actor, version, base_version=1, key="c-2", level="COMPARATIVE", baseline=7,
        signals=comparative_signals(),
    )
    assert created is True and row.declaration_level == "COMPARATIVE"


def test_authorization_pins_the_contract_version_and_no_second_declaration_reference_exists() -> None:
    from app.strategy.models import ExecutionAuthorization

    columns = set(ExecutionAuthorization.__table__.columns.keys())
    assert "contract_version_id" in columns
    assert not {c for c in columns if "declaration" in c or "specification" in c}


# --- Evidence Claim structural compatibility --------------------------------------------------------


def test_a_structured_claim_with_the_bound_metric_is_accepted(db_session) -> None:
    started = build_structured_started(db_session)
    entry = make_entry(db_session, started.campaign, values={"clicks": 5, "reach": 9}, channel="email")
    row, created = claim(db_session, started, entry=entry, metric_name="clicks")
    assert created is True and row.metric_name == "clicks"


def test_a_structured_claim_with_another_metric_is_typed_422_and_writes_nothing(db_session) -> None:
    from app.strategy.models import ExperimentEvidenceClaim

    started = build_structured_started(db_session)
    entry = make_entry(db_session, started.campaign, values={"clicks": 5, "reach": 9})
    with pytest.raises(EvidenceClaimMetricNotBoundError):
        claim(db_session, started, entry=entry, metric_name="reach")
    assert db_session.scalar(select(ExperimentEvidenceClaim.id).where(ExperimentEvidenceClaim.start_id == started.start.id)) is None


def test_metric_binding_is_case_sensitive(db_session) -> None:
    started = build_structured_started(db_session)
    entry = make_entry(db_session, started.campaign, values={"Clicks": 5})
    with pytest.raises(EvidenceClaimMetricNotBoundError):
        claim(db_session, started, entry=entry, metric_name="Clicks")


def test_an_exact_channel_binding_requires_the_exact_channel(db_session) -> None:
    started = build_structured_started(
        db_session, signals=[bound_signal(binding="EXACT", channel="email", min_points=1)]
    )
    good = make_entry(db_session, started.campaign, channel="email")
    bad = make_entry(db_session, started.campaign, channel="sms")
    wrong_case = make_entry(db_session, started.campaign, channel="Email")
    assert claim(db_session, started, entry=good)[1] is True
    with pytest.raises(EvidenceClaimChannelNotBoundError):
        claim(db_session, started, entry=bad)
    with pytest.raises(EvidenceClaimChannelNotBoundError):
        claim(db_session, started, entry=wrong_case)


def test_an_any_binding_accepts_every_channel(db_session) -> None:
    started = build_structured_started(db_session)
    for channel in ("email", "sms", "Some Odd Channel"):
        entry = make_entry(db_session, started.campaign, channel=channel)
        assert claim(db_session, started, entry=entry)[1] is True


def test_a_legacy_contract_keeps_the_previous_claim_behavior(db_session) -> None:
    started = build_started(db_session)  # LEGACY contract: no bindings
    entry = make_entry(db_session, started.campaign, values={"clicks": 5, "reach": 9}, channel="anything")
    assert claim(db_session, started, entry=entry, metric_name="reach")[1] is True
    assert claim(db_session, started, entry=entry, metric_name="clicks")[1] is True


def test_claim_replay_is_unchanged_by_the_binding_check(db_session) -> None:
    started = build_structured_started(db_session)
    entry = make_entry(db_session, started.campaign, channel="email")
    first, created = claim(db_session, started, entry=entry, key="k-1")
    again, created_again = claim(db_session, started, entry=entry, key="k-1")
    assert created is True and created_again is False and again.id == first.id


def test_active_datum_uniqueness_still_includes_the_signal_for_legacy_contracts(db_session) -> None:
    """PEMD-DFR-OBS-2: the EEB active-datum uniqueness includes required_signal_id, so the DATABASE does not
    prevent one datum being claimed under two signals. This gate adds no multi-signal datum exclusion (that
    belongs to future Measurement); a LEGACY contract still allows it exactly as before."""
    started = build_started(db_session, signal_names=("A", "B"))
    entry = make_entry(db_session, started.campaign, channel="email")
    assert claim(db_session, started, entry=entry, signal=started.signals[0])[1] is True
    assert claim(db_session, started, entry=entry, signal=started.signals[1])[1] is True


def test_in_a_structured_contract_a_datum_fits_only_the_signal_whose_slot_it_matches(db_session) -> None:
    """A consequence of the binding check plus slot exclusivity, not a new exclusion rule: with distinct EXACT
    channels the same datum is structurally compatible with exactly one signal."""
    started = build_structured_started(
        db_session,
        signals=[bound_signal("A", binding="EXACT", channel="email"), bound_signal("B", binding="EXACT", channel="sms")],
    )
    entry = make_entry(db_session, started.campaign, channel="email")
    assert claim(db_session, started, entry=entry, signal=started.signals[0])[1] is True
    with pytest.raises(EvidenceClaimChannelNotBoundError):
        claim(db_session, started, entry=entry, signal=started.signals[1])


# --- defense in depth for the binding slot ------------------------------------------------------------------------


def test_a_duplicate_slot_is_rejected_by_the_pure_check_before_any_write(db_session, monkeypatch) -> None:
    from app.strategy.repository import MeasurementContractRepository

    def never_write(self, **kwargs):  # pragma: no cover - reaching this is the failure
        raise AssertionError("the duplicate slot must be rejected before the repository is asked to write")

    campaign, experiment, actor, version = build_experiment_with_definition(db_session)
    monkeypatch.setattr(MeasurementContractRepository, "create", never_write)
    with pytest.raises(MeasurementContractBindingConflictError):
        declare(
            db_session, campaign, experiment, actor, version,
            signals=[bound_signal("A", binding="ANY"), bound_signal("B", binding="ANY")],
        )


def test_the_database_slot_index_violation_is_translated_to_the_typed_error_never_raised_raw(db_session, monkeypatch) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.strategy.repository import MeasurementContractRepository
    from tests.test_experiment_definition_domain import _integrity_error

    campaign, experiment, actor, version = build_experiment_with_definition(db_session)

    def boom(constraint):
        def create(self, **kwargs):
            raise _integrity_error(constraint)

        return create

    monkeypatch.setattr(MeasurementContractRepository, "create", boom("uq_contract_signals_binding_slot"))
    with pytest.raises(MeasurementContractBindingConflictError):
        declare(db_session, campaign, experiment, actor, version, signals=descriptive_signals())
    # Any OTHER unknown violation, including a sibling signal constraint, still re-raises as an invariant failure.
    for other in ("uq_measurement_contract_signals_contract_version_name", "ck_measurement_contract_signals_binding_copresent"):
        monkeypatch.setattr(MeasurementContractRepository, "create", boom(other))
        with pytest.raises(IntegrityError):
            declare(db_session, campaign, experiment, actor, version, key=f"k-{other}", signals=descriptive_signals())
