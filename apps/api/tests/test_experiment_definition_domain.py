"""Service-level tests for Governed Experiment Definition (MVP-37, frozen
MVP-37A/-37B): canonical lock order, the single writer, and the exact
IntegrityError translation contract. All marked `postgres`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit.models import AuditEvent
from app.core.api_errors import (
    ExperimentDefinitionBaseStaleError,
    ExperimentDefinitionUnchangedError,
    IdempotencyKeyConflictError,
)
from app.strategy.definition_service import (
    EVENT_EXPERIMENT_DEFINITION_DECLARED,
    EVENT_EXPERIMENT_DEFINITION_REVISED,
    ExperimentDefinitionService,
)
from app.strategy.models import ExperimentDefinitionVersion
from app.strategy.repository import (
    ExperimentDefinitionRepository,
    ExperimentRepository,
    StrategyRepository,
)
from tests.strategytest import build_current_experiment

pytestmark = pytest.mark.postgres

_UQ_VERSION = "uq_experiment_definition_versions_experiment_version"
_UQ_KEY = "uq_experiment_definition_versions_workspace_client_request_id"


def fields(**overrides: object) -> dict:
    payload = {
        "comparison_question": "Does a question hook change completion?",
        "comparison_type": "OBSERVATIONAL",
        "changed_factor": "Opening hook",
        "controlled_factors": [],
        "comparison_basis": "The current hook.",
        "scope": "Reels, one month.",
        "learning_intent": "Choose the next hook style.",
        "non_conclusion_boundary": "Does not establish causality.",
    }
    payload.update(overrides)
    return payload


def _write(session, campaign, experiment, actor, *, base_version=0, key="k-1", **overrides):
    return ExperimentDefinitionService(session).write_version(
        campaign=campaign,
        experiment_public_id=experiment.public_id,
        base_version=base_version,
        client_request_id=key,
        fields=fields(**overrides),
        actor_user_id=actor.id,
    )


def _integrity_error(constraint: str | None) -> IntegrityError:
    class _Diag:
        constraint_name = constraint

    class _Orig(Exception):
        diag = _Diag()

    return IntegrityError("INSERT INTO experiment_definition_versions", {}, _Orig())


def test_first_write_declares_and_second_revises(db_session) -> None:
    campaign, _strategy, _hypothesis, experiment, actor = build_current_experiment(db_session)
    first, created = _write(db_session, campaign, experiment, actor)
    assert created is True and first.version == 1 and first.public_id.startswith("EXD-")
    second, created = _write(db_session, campaign, experiment, actor, base_version=1, key="k-2", changed_factor="CTA")
    assert created is True and second.version == 2
    events = list(
        db_session.scalars(
            select(AuditEvent)
            .where(AuditEvent.experiment_id == experiment.id, AuditEvent.event_type.like("strategy.experiment_definition.%"))
            .order_by(AuditEvent.new_state)  # created_at ties inside one transaction (now() is transaction-scoped)
        )
    )
    assert [e.event_type for e in events] == [EVENT_EXPERIMENT_DEFINITION_DECLARED, EVENT_EXPERIMENT_DEFINITION_REVISED]


def test_service_raises_base_stale_and_unchanged_in_the_frozen_order(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    _write(db_session, campaign, experiment, actor)
    with pytest.raises(ExperimentDefinitionBaseStaleError):
        _write(db_session, campaign, experiment, actor, base_version=0, key="k-2")
    with pytest.raises(ExperimentDefinitionUnchangedError):
        _write(db_session, campaign, experiment, actor, base_version=1, key="k-3")
    with pytest.raises(IdempotencyKeyConflictError):
        _write(db_session, campaign, experiment, actor, base_version=0, key="k-1", scope="different")


def test_locks_are_taken_strategy_row_then_experiment_row_and_a_replay_takes_none(db_session, monkeypatch) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
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

    _write(db_session, campaign, experiment, actor)
    assert order == ["strategy", "experiment"]

    order.clear()
    _, created = _write(db_session, campaign, experiment, actor)  # matching replay
    assert created is False and order == []


def test_only_the_two_known_unique_violations_are_translated(db_session, monkeypatch) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)

    def boom(constraint):
        def create(self, **kwargs):
            raise _integrity_error(constraint)

        return create

    monkeypatch.setattr(ExperimentDefinitionRepository, "create", boom(_UQ_VERSION))
    with pytest.raises(ExperimentDefinitionBaseStaleError):
        _write(db_session, campaign, experiment, actor, key="ka")

    # Any other constraint (or none) is re-raised unchanged, never a domain conflict.
    for other in ("ck_experiment_definition_versions_version_positive", "uq_something_else", None):
        monkeypatch.setattr(ExperimentDefinitionRepository, "create", boom(other))
        with pytest.raises(IntegrityError):
            _write(db_session, campaign, experiment, actor, key=f"kb-{other}")

    # The client_request_id constraint with no winning row to re-read is also re-raised.
    monkeypatch.setattr(ExperimentDefinitionRepository, "create", boom(_UQ_KEY))
    with pytest.raises(IntegrityError):
        _write(db_session, campaign, experiment, actor, key="kc")


def test_writer_surface_is_append_only() -> None:
    public = {name for name in dir(ExperimentDefinitionRepository) if not name.startswith("_")}
    assert public == {
        "create", "get_by_workspace_and_request_id", "get_by_public_id_for_experiment", "get_tip",
        "list_for_experiment", "tips_for_experiments",
    }
    assert not {name for name in dir(ExperimentDefinitionService) if name.startswith(("update", "delete", "edit"))}
    assert hasattr(ExperimentDefinitionService, "write_version")


def test_no_lock_flag_or_variant_placeholder_exists_on_the_model() -> None:
    columns = set(ExperimentDefinitionVersion.__table__.columns.keys())
    assert not columns & {"is_locked", "is_frozen", "variant_id", "status", "updated_at", "created_by", "campaign_id"}
