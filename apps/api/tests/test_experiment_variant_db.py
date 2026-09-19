"""Structural database backstops for the ExperimentVariant table (MVP-38,
frozen MVP-38A/-38B): every test here BYPASSES the service and inserts
directly, proving the database itself — not application code — rejects the
violation. All marked `postgres`.

Honest limits, documented rather than hidden:
- the database cannot know which version is the current tip, so pinning a
  non-tip version is a SERVICE-level rule (the tip-only rule and the
  Definition lock rest on the Experiment row lock — MVP38A-OBS-2);
- normalized (casefold / whitespace-collapsed) label uniqueness is
  service-enforced; the database backstops only the exact string
  (MVP38A-OBS-1).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.audit.models import ActorType, AuditEvent
from app.core.ids import generate_public_id
from app.strategy.definition_service import ExperimentDefinitionService
from app.strategy.models import ExperimentDefinitionVersion, ExperimentVariant
from app.strategy.repository import ExperimentVariantRepository
from tests.strategytest import build_current_experiment
from tests.test_experiment_definition_domain import fields

pytestmark = pytest.mark.postgres

TEXT_FIELDS = ["label", "condition_description"]


def _version(session, campaign, experiment, actor, *, base_version=0, key="v-1", **overrides):
    version, _created = ExperimentDefinitionService(session).write_version(
        campaign=campaign, experiment_public_id=experiment.public_id, base_version=base_version,
        client_request_id=key, fields=fields(**overrides), actor_user_id=actor.id,
    )
    return version


def _row(version, **overrides) -> ExperimentVariant:
    values = dict(
        public_id=generate_public_id("VAR"), workspace_id=version.workspace_id, experiment_id=version.experiment_id,
        definition_version_id=version.id, ordinal=1, label="A", condition_description="d",
        client_request_id=uuid.uuid4().hex,
    )
    values.update(overrides)
    return ExperimentVariant(**values)


def _violation(session, row) -> str:
    with pytest.raises(IntegrityError) as excinfo:
        with session.begin_nested():
            session.add(row)
            session.flush()
    return excinfo.value.orig.diag.constraint_name


def test_duplicate_exact_label_is_rejected_by_the_database(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _version(db_session, campaign, experiment, actor)
    db_session.add(_row(version, ordinal=1, label="A"))
    db_session.flush()
    assert _violation(db_session, _row(version, ordinal=2, label="A")) == "uq_experiment_variants_definition_version_label"


def test_duplicate_ordinal_is_rejected_by_the_database(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _version(db_session, campaign, experiment, actor)
    db_session.add(_row(version, ordinal=1, label="A"))
    db_session.flush()
    assert _violation(db_session, _row(version, ordinal=1, label="B")) == "uq_experiment_variants_definition_version_ordinal"


def test_duplicate_workspace_client_request_id_is_rejected_by_the_database(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _version(db_session, campaign, experiment, actor)
    db_session.add(_row(version, ordinal=1, label="A", client_request_id="shared"))
    db_session.flush()
    assert _violation(db_session, _row(version, ordinal=2, label="B", client_request_id="shared")) == (
        "uq_experiment_variants_workspace_client_request_id"
    )


def test_the_client_request_id_and_labels_are_unique_per_scope_only(db_session) -> None:
    c1, _s1, _h1, first, a1 = build_current_experiment(db_session, campaign_name="Tenant One")
    c2, _s2, _h2, second, a2 = build_current_experiment(db_session, campaign_name="Tenant Two")
    assert first.workspace_id != second.workspace_id
    v1 = _version(db_session, c1, first, a1)
    v2 = _version(db_session, c2, second, a2, key="v-2")
    db_session.add(_row(v1, label="Same", client_request_id="shared"))
    db_session.add(_row(v2, label="Same", client_request_id="shared"))
    db_session.flush()  # same label and same key across workspaces/versions: no violation


def test_composite_fk_rejects_an_experiment_that_does_not_own_the_pinned_version(db_session) -> None:
    c1, _s1, _h1, first, a1 = build_current_experiment(db_session, campaign_name="Tenant One")
    _c2, _s2, _h2, second, _a2 = build_current_experiment(db_session, campaign_name="Tenant Two")
    version = _version(db_session, c1, first, a1)
    # experiment_id of ANOTHER Experiment, pinned to first's version.
    assert _violation(db_session, _row(version, experiment_id=second.id)) == (
        "fk_experiment_variants_definition_version_experiment_workspace"
    )


def test_composite_fk_rejects_a_workspace_that_does_not_own_the_pinned_version(db_session) -> None:
    c1, _s1, _h1, first, a1 = build_current_experiment(db_session, campaign_name="Tenant One")
    _c2, _s2, _h2, second, _a2 = build_current_experiment(db_session, campaign_name="Tenant Two")
    version = _version(db_session, c1, first, a1)
    assert _violation(db_session, _row(version, workspace_id=second.workspace_id)) == (
        "fk_experiment_variants_definition_version_experiment_workspace"
    )


def test_composite_fk_rejects_a_version_that_does_not_exist(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _version(db_session, campaign, experiment, actor)
    assert _violation(db_session, _row(version, definition_version_id=uuid.uuid4())) == (
        "fk_experiment_variants_definition_version_experiment_workspace"
    )


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"ordinal": 0}, "ck_experiment_variants_ordinal_positive"),
        ({"ordinal": -4}, "ck_experiment_variants_ordinal_positive"),
        *[({field: value}, "ck_experiment_variants_text_fields_nonblank") for field in TEXT_FIELDS for value in ("", "   ")],
    ],
)
def test_database_check_constraints_reject_invalid_rows(db_session, overrides, expected) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    version = _version(db_session, campaign, experiment, actor)
    assert _violation(db_session, _row(version, **overrides)) == expected


def test_the_candidate_key_exists_and_is_additive_on_the_version_table() -> None:
    names = {c.name for c in ExperimentDefinitionVersion.__table__.constraints if c.name}
    assert "uq_experiment_definition_versions_id_experiment_workspace" in names
    assert len("uq_experiment_definition_versions_id_experiment_workspace") <= 63


def test_database_does_not_know_which_version_is_the_tip(db_session) -> None:
    """Documents the honest limit: the tip-only rule and the Definition lock
    are SERVICE-level (Experiment row lock); a direct insert may pin an older
    version. MVP38A-OBS-2."""
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    v1 = _version(db_session, campaign, experiment, actor)
    _version(db_session, campaign, experiment, actor, base_version=1, key="v-2", changed_factor="Second")
    db_session.add(_row(v1, label="Historical pin"))
    db_session.flush()  # accepted by the database


def test_list_orders_by_version_then_ordinal_never_by_created_at(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    v1 = _version(db_session, campaign, experiment, actor)
    v2 = _version(db_session, campaign, experiment, actor, base_version=1, key="v-2", changed_factor="Second")
    # Insert in an order that differs from the expected read order (all share one transaction timestamp).
    for row in (
        _row(v2, ordinal=1, label="v2-1"), _row(v1, ordinal=2, label="v1-2"),
        _row(v2, ordinal=2, label="v2-2"), _row(v1, ordinal=1, label="v1-1"),
    ):
        db_session.add(row)
    db_session.flush()
    repo = ExperimentVariantRepository(db_session)
    page = repo.list_for_experiment(experiment_id=experiment.id, workspace_id=experiment.workspace_id, limit=10, offset=0)
    assert [variant.label for variant, _version_row in page] == ["v1-1", "v1-2", "v2-1", "v2-2"]
    windowed = repo.list_for_experiment(experiment_id=experiment.id, workspace_id=experiment.workspace_id, limit=2, offset=1)
    assert [variant.label for variant, _v in windowed] == ["v1-2", "v2-1"]
    assert repo.count_for_experiment(experiment_id=experiment.id, workspace_id=experiment.workspace_id) == 4
    counts = repo.counts_for_versions(definition_version_ids=[v1.id, v2.id], workspace_id=experiment.workspace_id)
    assert counts == {v1.id: 2, v2.id: 2}


def test_audit_link_column_is_a_nullable_fk_to_the_variant_table(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    event = AuditEvent(
        public_id=generate_public_id("AUDT"), workspace_id=campaign.workspace_id,
        event_type="strategy.variant.declared", actor_type=ActorType.USER, actor_user_id=actor.id,
        experiment_variant_id=uuid.uuid4(),
    )
    assert _violation(db_session, event) == "fk_audit_events_experiment_variant_id_experiment_variants"
    ok = AuditEvent(
        public_id=generate_public_id("AUDT"), workspace_id=campaign.workspace_id,
        event_type="unrelated.event", actor_type=ActorType.SYSTEM,
    )
    db_session.add(ok)
    db_session.flush()
    assert ok.experiment_variant_id is None


def test_columns_are_exactly_the_frozen_set_and_identifiers_fit_postgresql() -> None:
    table = ExperimentVariant.__table__
    assert set(table.columns.keys()) == {
        "id", "public_id", "workspace_id", "experiment_id", "definition_version_id", "ordinal", "label",
        "condition_description", "client_request_id", "created_at",
    }
    forbidden = {
        "role", "created_by", "updated_at", "status", "allocation", "weight", "traffic", "metric",
        "success_criterion", "exposure", "winner", "result",
    }
    assert not forbidden & set(table.columns.keys())
    for fk in table.foreign_key_constraints:
        assert fk.ondelete is None and fk.onupdate is None
        assert len(fk.name) <= 63
    names = [c.name for c in table.constraints if c.name] + [i.name for i in table.indexes]
    assert all(len(str(name)) <= 63 for name in names), [n for n in names if len(str(n)) > 63]
    assert "fk_experiment_variants_definition_version_experiment_workspace" in {c.name for c in table.constraints}
