"""Service-level tests for Governed Variant Identity (MVP-38, frozen
MVP-38A/-38B): the frozen write order, canonical lock order, the exact
IntegrityError translation contract, the central Definition-lock seam, and
the append-only writer surface. All marked `postgres`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit.models import AuditEvent
from app.core.api_errors import (
    ExperimentDefinitionPinnedError,
    ExperimentVariantDefinitionVersionNotCurrentError,
    ExperimentVariantLabelDuplicateError,
    ForbiddenError,
    IdempotencyKeyConflictError,
)
from app.strategy.definition_service import ExperimentDefinitionService
from app.strategy.repository import ExperimentRepository, ExperimentVariantRepository, StrategyRepository
from app.strategy.variant_service import ExperimentVariantService, label_key
from tests.strategytest import build_current_experiment
from tests.test_experiment_definition_domain import _integrity_error, fields

pytestmark = pytest.mark.postgres

_UQ_LABEL = "uq_experiment_variants_definition_version_label"
_UQ_KEY = "uq_experiment_variants_workspace_client_request_id"
_UQ_ORDINAL = "uq_experiment_variants_definition_version_ordinal"


def _define(session, campaign, experiment, actor, *, base_version=0, key="d-1", **overrides):
    return ExperimentDefinitionService(session).write_version(
        campaign=campaign, experiment_public_id=experiment.public_id, base_version=base_version,
        client_request_id=key, fields=fields(**overrides), actor_user_id=actor.id,
    )[0]


def _declare(session, campaign, experiment, actor, version, *, label="A", description="d", key="k-1"):
    return ExperimentVariantService(session).declare_variant(
        campaign=campaign, experiment_public_id=experiment.public_id, definition_version_public_id=version.public_id,
        label=label, condition_description=description, client_request_id=key, actor_user_id=actor.id,
    )


def test_label_key_collapses_whitespace_and_case_without_being_stored() -> None:
    assert label_key("  Variant   A ") == label_key("variant a") == "variant a"
    assert label_key("Straße") == label_key("STRASSE")  # casefold, not lower
    assert label_key("A") != label_key("B")


def test_declaration_assigns_sequential_ordinals_and_pins_the_requested_version(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)
    first, pinned, created = _declare(db_session, campaign, experiment, actor, version, label="A", key="k-1")
    second, _pinned, _created = _declare(db_session, campaign, experiment, actor, version, label="B", key="k-2")
    assert created is True and pinned.id == version.id
    assert (first.ordinal, second.ordinal) == (1, 2)
    assert first.public_id.startswith("VAR-")
    events = list(
        db_session.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "strategy.variant.declared", AuditEvent.experiment_id == experiment.id)
        )
    )
    assert len(events) == 2 and all(e.new_state == "pinned:v1" and e.previous_state is None for e in events)


def test_a_version_of_another_experiment_or_no_definition_is_a_non_leaky_forbidden(db_session) -> None:
    campaign, _s, hypothesis, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)
    from app.strategy.service import StrategyService

    other = StrategyService(db_session).create_experiment(
        campaign=campaign, hypothesis_public_id=hypothesis.public_id, description="other", actor_user_id=actor.id
    )
    with pytest.raises(ForbiddenError):
        _declare(db_session, campaign, other, actor, version)  # the EXD belongs to the first Experiment


def test_stale_pin_duplicate_and_conflict_errors_in_the_frozen_order(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    v1 = _define(db_session, campaign, experiment, actor)
    v2 = _define(db_session, campaign, experiment, actor, base_version=1, key="d-2", changed_factor="Second")
    with pytest.raises(ExperimentVariantDefinitionVersionNotCurrentError):
        _declare(db_session, campaign, experiment, actor, v1)
    _declare(db_session, campaign, experiment, actor, v2, label="Same", key="k-1")
    with pytest.raises(ExperimentVariantLabelDuplicateError):
        _declare(db_session, campaign, experiment, actor, v2, label=" same ", key="k-2")
    with pytest.raises(IdempotencyKeyConflictError):
        _declare(db_session, campaign, experiment, actor, v2, label="Different", key="k-1")


def test_locks_are_taken_strategy_row_then_experiment_row_and_a_replay_takes_none(db_session, monkeypatch) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)
    order: list[str] = []
    real_strategy_get = StrategyRepository.get_by_id
    real_experiment_get = ExperimentRepository.get_by_id

    def strategy_get(self, strategy_id, *, for_update=False):
        if for_update:
            order.append("strategy")
        return real_strategy_get(self, strategy_id, for_update=for_update)

    def experiment_get(self, experiment_id, *, for_update=False):
        if for_update:
            order.append("experiment")
        return real_experiment_get(self, experiment_id, for_update=for_update)

    monkeypatch.setattr(StrategyRepository, "get_by_id", strategy_get)
    monkeypatch.setattr(ExperimentRepository, "get_by_id", experiment_get)

    _declare(db_session, campaign, experiment, actor, version)
    assert order == ["strategy", "experiment"]
    order.clear()
    _row, _pinned, created = _declare(db_session, campaign, experiment, actor, version)  # matching replay
    assert created is False and order == []


def test_only_the_two_known_unique_violations_are_translated(db_session, monkeypatch) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)

    def boom(constraint):
        def create(self, **kwargs):
            raise _integrity_error(constraint)

        return create

    monkeypatch.setattr(ExperimentVariantRepository, "create", boom(_UQ_LABEL))
    with pytest.raises(ExperimentVariantLabelDuplicateError):
        _declare(db_session, campaign, experiment, actor, version, key="ka")

    # Any other constraint — INCLUDING an ordinal collision, which the Experiment lock makes
    # impossible — or no constraint at all is re-raised unchanged: an invariant failure.
    for other in (_UQ_ORDINAL, "ck_experiment_variants_ordinal_positive", "uq_something_else", None):
        monkeypatch.setattr(ExperimentVariantRepository, "create", boom(other))
        with pytest.raises(IntegrityError):
            _declare(db_session, campaign, experiment, actor, version, key=f"kb-{other}")

    # The client_request_id constraint with no winning row to re-read is also re-raised.
    monkeypatch.setattr(ExperimentVariantRepository, "create", boom(_UQ_KEY))
    with pytest.raises(IntegrityError):
        _declare(db_session, campaign, experiment, actor, version, key="kc")


def test_the_definition_lock_is_decided_only_by_the_central_seam(db_session, monkeypatch) -> None:
    """A future pinning child integrates ONLY by extending
    ``_has_pinning_children`` — proven by making the seam say "pinned" with no
    Variant present, and by the writer honouring it before the unchanged check."""
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    _define(db_session, campaign, experiment, actor)
    monkeypatch.setattr(ExperimentDefinitionService, "_has_pinning_children", lambda self, **kw: True)
    with pytest.raises(ExperimentDefinitionPinnedError):
        _define(db_session, campaign, experiment, actor, base_version=1, key="d-2", changed_factor="Blocked")
    # An equivalent replay is decided BEFORE the seam and still succeeds.
    version, created = ExperimentDefinitionService(db_session).write_version(
        campaign=campaign, experiment_public_id=experiment.public_id, base_version=0, client_request_id="d-1",
        fields=fields(), actor_user_id=actor.id,
    )
    assert created is False and version.version == 1


def test_the_pin_state_is_derived_and_batched(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _define(db_session, campaign, experiment, actor)
    service = ExperimentDefinitionService(db_session)
    states = service.pin_states_for_versions(workspace_id=campaign.workspace_id, definition_version_ids=[version.id])
    assert states == {version.id: (0, False)}
    _declare(db_session, campaign, experiment, actor, version, label="A", key="k-1")
    _declare(db_session, campaign, experiment, actor, version, label="B", key="k-2")
    states = service.pin_states_for_versions(workspace_id=campaign.workspace_id, definition_version_ids=[version.id])
    assert states == {version.id: (2, True)}
    assert service.pin_states_for_versions(workspace_id=campaign.workspace_id, definition_version_ids=[]) == {}


def test_writer_surface_is_append_only_and_has_no_lifecycle() -> None:
    public = {name for name in dir(ExperimentVariantRepository) if not name.startswith("_")}
    assert public == {
        "count_for_experiment", "counts_for_versions", "create", "exists_for_definition_version",
        "get_by_workspace_and_request_id", "list_for_experiment", "list_labels_for_version",
        "max_ordinal_for_version",
    }
    assert not {n for n in dir(ExperimentVariantService) if n.startswith(("update", "delete", "edit", "correct", "retire", "supersede"))}
    assert hasattr(ExperimentVariantService, "declare_variant")
