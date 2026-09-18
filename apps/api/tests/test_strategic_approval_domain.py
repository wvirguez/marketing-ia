"""Domain/service-level tests for StrategicApproval (MVP-29B, implementing
the frozen MVP-29A contract). All marked `postgres` — these exercise
``StrategicApprovalService`` directly against real domain objects, the
same pattern established throughout ``tests/test_strategic_decision_domain.py``.
"""

from __future__ import annotations

import pytest

from app.core.api_errors import (
    ForbiddenError,
    StrategicApprovalAlreadyExistsError,
    StrategicDecisionNotEligibleForApprovalError,
)
from app.orchestration.models import StrategicApprovalOutcome, StrategicDecisionType
from app.orchestration.repository import StrategicApprovalRepository
from app.orchestration.service import StrategicApprovalService, StrategicDecisionService
from tests.commercialtest import build_campaign
from tests.contenttest import make_user
from tests.orchestrationtest import build_strategic_approval, build_strategic_decision

pytestmark = pytest.mark.postgres


# --- eligibility gate: ADOPT + current only ---------------------------------


def test_recording_an_approval_for_an_adopt_decision_succeeds(db_session) -> None:
    campaign, _recommendation, decision, actor = build_strategic_decision(
        db_session, decision_type=StrategicDecisionType.ADOPT
    )
    approval = StrategicApprovalService(db_session).record_approval(
        campaign=campaign,
        decision_public_id=decision.public_id,
        outcome=StrategicApprovalOutcome.APPROVED,
        actor_user_id=actor.id,
    )
    assert approval.strategic_decision_id == decision.id
    assert approval.outcome is StrategicApprovalOutcome.APPROVED
    assert approval.public_id.startswith("SAP-")


def test_recording_a_rejected_approval_succeeds(db_session) -> None:
    campaign, _recommendation, decision, actor = build_strategic_decision(
        db_session, decision_type=StrategicDecisionType.ADOPT
    )
    approval = StrategicApprovalService(db_session).record_approval(
        campaign=campaign,
        decision_public_id=decision.public_id,
        outcome=StrategicApprovalOutcome.REJECTED,
        actor_user_id=actor.id,
    )
    assert approval.outcome is StrategicApprovalOutcome.REJECTED


def test_recording_an_approval_for_a_defer_decision_is_ineligible(db_session) -> None:
    campaign, _recommendation, decision, actor = build_strategic_decision(
        db_session, decision_type=StrategicDecisionType.DEFER
    )
    with pytest.raises(StrategicDecisionNotEligibleForApprovalError):
        StrategicApprovalService(db_session).record_approval(
            campaign=campaign,
            decision_public_id=decision.public_id,
            outcome=StrategicApprovalOutcome.APPROVED,
            actor_user_id=actor.id,
        )


def test_recording_an_approval_for_a_decline_decision_is_ineligible(db_session) -> None:
    campaign, _recommendation, decision, actor = build_strategic_decision(
        db_session, decision_type=StrategicDecisionType.DECLINE
    )
    with pytest.raises(StrategicDecisionNotEligibleForApprovalError):
        StrategicApprovalService(db_session).record_approval(
            campaign=campaign,
            decision_public_id=decision.public_id,
            outcome=StrategicApprovalOutcome.APPROVED,
            actor_user_id=actor.id,
        )


def test_recording_an_approval_for_an_already_superseded_decision_is_ineligible(db_session) -> None:
    campaign, _recommendation, original, actor = build_strategic_decision(
        db_session, decision_type=StrategicDecisionType.ADOPT
    )
    StrategicDecisionService(db_session).supersede_decision(
        campaign=campaign, decision_public_id=original.public_id, decision_type=StrategicDecisionType.DEFER,
        statement="Reconsidered.", actor_user_id=actor.id,
    )
    with pytest.raises(StrategicDecisionNotEligibleForApprovalError):
        StrategicApprovalService(db_session).record_approval(
            campaign=campaign,
            decision_public_id=original.public_id,
            outcome=StrategicApprovalOutcome.APPROVED,
            actor_user_id=actor.id,
        )


def test_recording_an_approval_for_a_nonexistent_decision_is_forbidden_not_a_bare_404(db_session) -> None:
    campaign = build_campaign(db_session)
    with pytest.raises(ForbiddenError):
        StrategicApprovalService(db_session).record_approval(
            campaign=campaign, decision_public_id="DEC-DOESNOTEXIST",
            outcome=StrategicApprovalOutcome.APPROVED, actor_user_id=make_user(db_session).id,
        )


# --- cardinality: at most one Approval per Decision -------------------------


def test_second_approval_attempt_for_the_same_decision_is_a_deterministic_conflict(db_session) -> None:
    campaign, _recommendation, decision, _approval, actor = build_strategic_approval(db_session)
    with pytest.raises(StrategicApprovalAlreadyExistsError):
        StrategicApprovalService(db_session).record_approval(
            campaign=campaign, decision_public_id=decision.public_id,
            outcome=StrategicApprovalOutcome.REJECTED, actor_user_id=actor.id,
        )


def test_current_approval_query_returns_exactly_one_row(db_session) -> None:
    campaign, _recommendation, decision, approval, _actor = build_strategic_approval(db_session)
    current = StrategicApprovalRepository(db_session).get_for_decision(decision_id=decision.id)
    assert current is not None and current.id == approval.id


# --- immutability -------------------------------------------------------------


def test_no_update_or_delete_method_exists_for_the_approval_repository() -> None:
    assert not hasattr(StrategicApprovalRepository, "update")
    assert not hasattr(StrategicApprovalRepository, "delete")


def test_approval_outcome_is_never_mutated(db_session) -> None:
    campaign, _recommendation, decision, approval, _actor = build_strategic_approval(
        db_session, outcome=StrategicApprovalOutcome.APPROVED
    )
    original_outcome = approval.outcome
    db_session.refresh(approval)
    assert approval.outcome == original_outcome


def test_no_decision_type_field_exists_on_approval() -> None:
    """The outcome vocabulary belongs to StrategicApproval alone
    (APPROVED/REJECTED) — it never duplicates StrategicDecision's own
    ``decision_type`` column (ADOPT/DEFER/DECLINE)."""
    from app.orchestration.models import StrategicApproval

    assert "decision_type" not in StrategicApproval.__table__.columns.keys()


# --- decision supersession interaction (MVP-29A §L, MVP-29B §12/§13) --------


def test_approval_survives_decision_supersession_as_historical_fact(db_session) -> None:
    campaign, _recommendation, original, approval, actor = build_strategic_approval(db_session)
    StrategicDecisionService(db_session).supersede_decision(
        campaign=campaign, decision_public_id=original.public_id, decision_type=StrategicDecisionType.DEFER,
        statement="Reconsidered after approval.", actor_user_id=actor.id,
    )
    reread = StrategicApprovalRepository(db_session).get_for_decision(decision_id=original.id)
    assert reread is not None
    assert reread.id == approval.id
    assert reread.outcome == approval.outcome


def test_superseding_decision_never_creates_or_copies_an_approval_for_the_replacement(db_session) -> None:
    campaign, _recommendation, original, _approval, actor = build_strategic_approval(db_session)
    replacement = StrategicDecisionService(db_session).supersede_decision(
        campaign=campaign, decision_public_id=original.public_id, decision_type=StrategicDecisionType.ADOPT,
        statement="New ADOPT version.", actor_user_id=actor.id,
    )
    inherited = StrategicApprovalRepository(db_session).get_for_decision(decision_id=replacement.id)
    assert inherited is None  # never auto-inherited (MVP-29A §O)


def test_superseding_an_approved_decision_does_not_fail_or_require_removing_the_approval(db_session) -> None:
    """MVP-29B §12: existing Approval must never block Decision
    supersession — the two entities are independently governed."""
    campaign, _recommendation, original, _approval, actor = build_strategic_approval(db_session)
    replacement = StrategicDecisionService(db_session).supersede_decision(
        campaign=campaign, decision_public_id=original.public_id, decision_type=StrategicDecisionType.DEFER,
        statement="x", actor_user_id=actor.id,
    )
    assert replacement.superseded_at is None
    db_session.refresh(original)
    assert original.superseded_at is not None


# --- tenancy: non-leaky campaign-scoped resolution --------------------------


def test_recording_an_approval_against_a_decision_from_a_different_campaign_is_forbidden(db_session) -> None:
    campaign_a, _recommendation, decision_a, actor = build_strategic_decision(
        db_session, decision_type=StrategicDecisionType.ADOPT, campaign_name="Campaign A"
    )
    campaign_b = build_campaign(db_session, org_name="Other Org", workspace_name="Other WS", campaign_name="Campaign B")
    with pytest.raises(ForbiddenError):
        StrategicApprovalService(db_session).record_approval(
            campaign=campaign_b, decision_public_id=decision_a.public_id,
            outcome=StrategicApprovalOutcome.APPROVED, actor_user_id=actor.id,
        )


# --- legacy compatibility (MVP-29A §W) --------------------------------------


def test_adopt_decision_without_an_approval_remains_valid_and_unbackfilled(db_session) -> None:
    campaign, _recommendation, decision, _actor = build_strategic_decision(
        db_session, decision_type=StrategicDecisionType.ADOPT
    )
    current = StrategicApprovalRepository(db_session).get_for_decision(decision_id=decision.id)
    assert current is None  # no fabricated Approval


def test_campaign_with_no_strategic_approvals_remains_fully_readable(db_session) -> None:
    campaign = build_campaign(db_session)
    assert StrategicApprovalService(db_session).list_approvals_for_campaign(campaign.id) == []


# --- mandatory human actor ----------------------------------------------------


def test_actor_user_id_is_mandatory_never_defaulted() -> None:
    import inspect

    params = inspect.signature(StrategicApprovalService.record_approval).parameters
    assert params["actor_user_id"].default is inspect.Parameter.empty
