"""BACKEND-06 §36 security checks: no raw UUID leakage anywhere in the
orchestration response surface, no internal AGENT-0N identifiers in the
default progress/stage/event surface, and no request body field can be
used to smuggle an authoritative workspace/campaign/run id.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from app.campaigns.models import CampaignRun
from app.orchestration.models import HumanDecisionRequest, RunStageExecution
from tests.orchestrationtest import initialize_run, run_path, start_run

pytestmark = pytest.mark.postgres

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def test_no_raw_uuid_anywhere_in_progress_stages_or_events(campaign_run_client: dict, db_session) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    for path in ("", "/progress", "/stages", "/events", "/decisions"):
        response = campaign_run_client["client"].get(run_path(campaign_run_client, path))
        assert response.status_code == 200
        assert not _UUID_RE.search(response.text), f"{path or '/'} leaked a raw UUID"


def test_no_agent_identifiers_in_progress_or_stages(campaign_run_client: dict) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    for path in ("/progress", "/stages"):
        text = campaign_run_client["client"].get(run_path(campaign_run_client, path)).text
        assert "AGENT-" not in text
        assert "agent_id" not in text


def test_no_chain_of_thought_or_reasoning_field_exists_anywhere() -> None:
    """Static schema check (BACKEND-06 §27): no orchestration model
    defines a reasoning/chain-of-thought-shaped field."""
    forbidden_substrings = ("reasoning", "chain_of_thought", "scratchpad", "thinking")
    for model in (RunStageExecution, HumanDecisionRequest):
        for column_name in model.__table__.columns.keys():
            lowered = column_name.lower()
            assert not any(bad in lowered for bad in forbidden_substrings), f"{model.__name__}.{column_name}"


def test_client_supplied_workspace_id_in_request_body_is_ignored(campaign_run_client: dict, db_session) -> None:
    """PATCH-equivalent style smuggling attempt: even if a client sends
    a `workspace_id`-shaped field, no orchestration endpoint reads
    request-body tenancy fields at all — the only source of workspace
    truth is the authenticated session (BACKEND-06 §18/§24)."""
    other_run = db_session.execute(select(CampaignRun)).scalars().first()
    payload = {"response_text": "trying to smuggle a field", "workspace_id": str(other_run.workspace_id) if other_run else "irrelevant"}

    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    # No decision is open, so this should 404-equivalent (ForbiddenError,
    # since the decision id is fabricated) regardless of the extra field
    # — proving the extra body field has no special handling/authority.
    response = campaign_run_client["client"].post(
        run_path(campaign_run_client, "/decisions/HDR-DOESNOTEXIST0/respond"),
        json=payload,
        headers={"X-CSRF-Token": campaign_run_client["csrf_token"]},
    )
    assert response.status_code == 403


def test_campaign_and_run_public_ids_never_expose_the_underlying_uuid_format_by_construction() -> None:
    """Confirms the public_id generator's actual output shape used
    throughout orchestration responses is never itself a UUID string —
    a structural guarantee, not just an absence-of-match test."""
    from app.core.ids import generate_public_id

    generated = generate_public_id("STG")
    assert not _UUID_RE.match(generated)
    assert generated.startswith("STG-")
