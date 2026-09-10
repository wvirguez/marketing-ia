"""Shared helpers/fixtures for Assets tests — real database required. No
public write endpoint exists in BACKEND-13, so most tests exercise
``AssetsService`` directly against real domain objects, the same
"construct a controlled fixture" pattern already used throughout
``tests/contenttest.py``/``tests/measurementtest.py``.
"""

from __future__ import annotations

from app.assets.service import AssetsService
from app.content.service import ContentService
from tests.contenttest import build_plan_with_item, default_piece_fields, default_version_payload


def default_creative_brief_spec(**overrides: object) -> dict:
    payload = {"tone": "playful", "must_include": ["dog", "leash"], "avoid": ["competitor names"]}
    payload.update(overrides)
    return payload


def default_asset_fields(**overrides: object) -> dict:
    payload = {"kind": "image", "status": None, "storage_reference": "s3://bucket/key.png", "metadata": {"width": 1080}}
    payload.update(overrides)
    return payload


def build_content_piece(session, *, org_name="Assets Org", workspace_name="Assets WS", campaign_name="Assets Campaign"):
    """Creates the full ancestry (Organization/Workspace/Campaign/
    CampaignRun/RunStageExecutions/ContentPlan/PlanItem/ContentBrief/
    ContentPiece) needed to reach a ContentPiece a CreativeBrief can
    attach to. Returns ``(campaign, content_campaign, brief, piece,
    version)``."""
    content_campaign = build_plan_with_item(
        session, org_name=org_name, workspace_name=workspace_name, campaign_name=campaign_name
    )
    campaign, _run, _stages, plan, item = content_campaign
    content_brief = ContentService(session).record_brief(plan_item=item, content_plan=plan, brief="Produce a reel.")
    piece, version = ContentService(session).record_piece(
        content_brief=content_brief, initial_payload=default_version_payload(), **default_piece_fields()
    )
    return campaign, content_campaign, content_brief, piece, version


def build_creative_brief(session, **overrides: object):
    campaign, content_campaign, _content_brief, piece, _version = build_content_piece(session, **overrides)
    creative_brief = AssetsService(session).record_creative_brief(content_piece=piece, spec=default_creative_brief_spec())
    return campaign, piece, creative_brief


def build_asset(session, **overrides: object):
    campaign, piece, creative_brief = build_creative_brief(session, **overrides)
    fields = default_asset_fields()
    asset, version = AssetsService(session).record_asset(creative_brief=creative_brief, **fields)
    return campaign, piece, creative_brief, asset, version
