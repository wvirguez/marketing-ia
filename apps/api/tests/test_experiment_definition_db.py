"""Structural database backstops for the Experiment Definition version table
(MVP-37, frozen MVP-37A/-37B): every test here BYPASSES the service and
inserts directly, proving the database itself — not application code —
rejects the violation. All marked `postgres`.

Honest limit (MVP37B-OBS-1): PostgreSQL CHECK cannot inspect JSONB array
ELEMENTS, so the database does NOT enforce that controlled_factors items are
strings — ``test_database_does_not_enforce_controlled_factor_element_types``
documents that gap instead of hiding it.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.audit.models import ActorType, AuditEvent
from app.core.ids import generate_public_id
from app.strategy.models import ExperimentDefinitionVersion
from tests.strategytest import build_current_experiment

pytestmark = pytest.mark.postgres

TABLE = "experiment_definition_versions"
TEXT_FIELDS = [
    "comparison_question", "changed_factor", "comparison_basis", "scope", "learning_intent", "non_conclusion_boundary",
]


def _row(experiment, **overrides) -> ExperimentDefinitionVersion:
    values = dict(
        public_id=generate_public_id("EXD"), workspace_id=experiment.workspace_id, experiment_id=experiment.id,
        version=1, client_request_id=uuid.uuid4().hex, comparison_question="q", comparison_type="OBSERVATIONAL",
        changed_factor="f", controlled_factors=[], comparison_basis="b", scope="s", learning_intent="l",
        non_conclusion_boundary="n",
    )
    values.update(overrides)
    return ExperimentDefinitionVersion(**values)


def _violation(session, row) -> str:
    """Returns the violated constraint's name; fails if the insert succeeds."""
    with pytest.raises(IntegrityError) as excinfo:
        with session.begin_nested():
            session.add(row)
            session.flush()
    return excinfo.value.orig.diag.constraint_name


def test_duplicate_experiment_version_is_rejected_by_the_database(db_session) -> None:
    _c, _s, _h, experiment, _a = build_current_experiment(db_session)
    db_session.add(_row(experiment, version=1))
    db_session.flush()
    assert _violation(db_session, _row(experiment, version=1)) == (
        "uq_experiment_definition_versions_experiment_version"
    )


def test_duplicate_workspace_client_request_id_is_rejected_by_the_database(db_session) -> None:
    _c, _s, _h, experiment, _a = build_current_experiment(db_session)
    db_session.add(_row(experiment, version=1, client_request_id="shared"))
    db_session.flush()
    assert _violation(db_session, _row(experiment, version=2, client_request_id="shared")) == (
        "uq_experiment_definition_versions_workspace_client_request_id"
    )


def test_the_client_request_id_is_unique_per_workspace_only(db_session) -> None:
    _c, _s, _h, first, _a = build_current_experiment(db_session, campaign_name="Tenant One")
    _c2, _s2, _h2, second, _a2 = build_current_experiment(db_session, campaign_name="Tenant Two")
    assert first.workspace_id != second.workspace_id
    db_session.add(_row(first, client_request_id="shared"))
    db_session.add(_row(second, client_request_id="shared"))
    db_session.flush()  # no violation across workspaces


def test_composite_tenant_fk_rejects_a_workspace_that_does_not_own_the_experiment(db_session) -> None:
    _c, _s, _h, first, _a = build_current_experiment(db_session, campaign_name="Tenant One")
    _c2, _s2, _h2, second, _a2 = build_current_experiment(db_session, campaign_name="Tenant Two")
    assert _violation(db_session, _row(first, workspace_id=second.workspace_id)) == (
        "fk_experiment_definition_versions_experiment_workspace"
    )


@pytest.mark.parametrize(
    "overrides,allowed",
    [
        ({"controlled_factors": {"a": 1}}, {"ck_experiment_definition_versions_controlled_factors_is_array", "ck_experiment_definition_versions_controlled_factors_max_count"}),
        ({"controlled_factors": "text"}, {"ck_experiment_definition_versions_controlled_factors_is_array", "ck_experiment_definition_versions_controlled_factors_max_count"}),
        ({"controlled_factors": None}, {"ck_experiment_definition_versions_controlled_factors_is_array", "ck_experiment_definition_versions_controlled_factors_max_count"}),  # JSON null
        ({"comparison_type": "CONTROLLED", "controlled_factors": []}, {"ck_experiment_definition_versions_controlled_needs_factors"}),
        ({"controlled_factors": [f"f{i}" for i in range(21)]}, {"ck_experiment_definition_versions_controlled_factors_max_count"}),
        ({"version": 0}, {"ck_experiment_definition_versions_version_positive"}),
        ({"version": -3}, {"ck_experiment_definition_versions_version_positive"}),
        ({"comparison_type": "RANDOMIZED"}, {"ck_experiment_definition_versions_comparison_type_valid"}),
        ({"comparison_type": "controlled"}, {"ck_experiment_definition_versions_comparison_type_valid"}),
        *[({field: value}, {"ck_experiment_definition_versions_text_fields_nonblank"}) for field in TEXT_FIELDS for value in ("", "   ")],
    ],
)
def test_database_check_constraints_reject_invalid_rows(db_session, overrides, allowed) -> None:
    _c, _s, _h, experiment, _a = build_current_experiment(db_session)
    assert _violation(db_session, _row(experiment, **overrides)) in allowed


def test_a_valid_controlled_row_and_the_exact_bounds_are_accepted(db_session) -> None:
    _c, _s, _h, experiment, _a = build_current_experiment(db_session)
    db_session.add(
        _row(experiment, comparison_type="CONTROLLED", controlled_factors=[f"f{i}" for i in range(20)])
    )
    db_session.flush()


def test_database_does_not_enforce_controlled_factor_element_types(db_session) -> None:
    """MVP37B-OBS-1: element typing is a service/schema-layer invariant only.
    This test pins the known gap so it cannot be silently mistaken for a
    database guarantee."""
    _c, _s, _h, experiment, _a = build_current_experiment(db_session)
    db_session.add(_row(experiment, controlled_factors=[1, {"a": 2}, None]))
    db_session.flush()


def test_audit_link_column_is_a_nullable_fk_to_the_version_table(db_session) -> None:
    campaign, _s, _h, experiment, actor = build_current_experiment(db_session)
    event = AuditEvent(
        public_id=generate_public_id("AUDT"), workspace_id=campaign.workspace_id,
        event_type="strategy.experiment_definition.declared", actor_type=ActorType.USER, actor_user_id=actor.id,
        experiment_definition_version_id=uuid.uuid4(),
    )
    assert _violation(db_session, event) == "fk_audit_events_experiment_definition_version_id"
    ok = AuditEvent(
        public_id=generate_public_id("AUDT"), workspace_id=campaign.workspace_id,
        event_type="unrelated.event", actor_type=ActorType.SYSTEM,
    )
    db_session.add(ok)
    db_session.flush()
    assert ok.experiment_definition_version_id is None


def test_no_foreign_key_cascades_and_identifiers_fit_postgresql() -> None:
    table = ExperimentDefinitionVersion.__table__
    for fk in table.foreign_key_constraints:
        assert fk.ondelete is None and fk.onupdate is None
        assert len(fk.name) <= 63
    audit_fk = next(
        fk for fk in AuditEvent.__table__.foreign_key_constraints
        if [c.name for c in fk.columns] == ["experiment_definition_version_id"]
    )
    assert audit_fk.name == "fk_audit_events_experiment_definition_version_id" and audit_fk.ondelete is None
    names = [c.name for c in table.constraints if c.name] + [i.name for i in table.indexes]
    assert all(len(str(name)) <= 63 for name in names), [n for n in names if len(str(n)) > 63]
