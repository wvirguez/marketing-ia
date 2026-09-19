"""Real PostgreSQL concurrency proof for Governed Content Piece creation
(MVP-35B, frozen MVP-35A contract).

Unlike Content Brief's concurrency contract (1-Piece-per-Brief-style
1:0..1 races, one ok / one conflict), Content Piece is deliberately
``ContentBrief 1 -> 0..N ContentPiece`` with no DB uniqueness constraint
on ``content_brief_id`` (MVP-35A §D/§U) — two genuinely concurrent
governed creates against the same Brief are BOTH expected to succeed
independently, never conflict. This test proves that directly, rather
than assuming it by inheriting ContentBrief's own concurrency shape.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import ActorType, AuditEvent
from app.content.models import ContentPiece, ContentVersion
from app.content.repository import ContentBriefRepository
from app.content.service import ContentService
from tests.contenttest import build_plan_with_item, default_piece_fields, default_version_payload, make_user
from tests.test_content_plan_concurrency import _run_two

pytestmark = pytest.mark.postgres


def _run_iteration(postgres_engine, *, campaign_id, content_brief_public_id: str, actor_id) -> tuple[list[str], list[Exception]]:
    def operation(session: Session, which: int) -> None:
        brief = ContentBriefRepository(session).get_for_campaign_by_public_id(
            campaign_id=campaign_id, public_id=content_brief_public_id
        )
        fields = default_piece_fields()
        ContentService(session).record_piece(
            content_brief=brief, initial_payload=default_version_payload(),
            created_by_user_id=actor_id, actor_user_id=actor_id, **fields,
        )

    outcomes, errors, backend_pids = _run_two(postgres_engine, operation)
    assert not errors
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
    return outcomes, errors


def test_two_concurrent_piece_creates_for_the_same_brief_both_succeed(postgres_engine) -> None:
    with Session(postgres_engine) as setup:
        campaign, _run, _stages, plan, plan_item = build_plan_with_item(
            setup, campaign_name="Concurrency Piece Campaign"
        )
        brief = ContentService(setup).record_brief(plan_item=plan_item, content_plan=plan, brief="Concurrency brief.")
        actor = make_user(setup)
        setup.commit()
        campaign_id = campaign.id
        content_brief_public_id = brief.public_id
        actor_id = actor.id

    results = []
    for iteration in range(3):
        outcomes, _errors = _run_iteration(
            postgres_engine, campaign_id=campaign_id, content_brief_public_id=content_brief_public_id, actor_id=actor_id
        )
        results.append(outcomes)
        # Deliberately different from ContentBrief's own 1-ok/1-conflict
        # shape: both concurrent creates must succeed independently.
        assert outcomes.count("ok") == 2
        assert outcomes.count("conflict") == 0

    with Session(postgres_engine) as check:
        brief_row = ContentBriefRepository(check).get_for_campaign_by_public_id(
            campaign_id=campaign_id, public_id=content_brief_public_id
        )
        pieces = list(check.scalars(select(ContentPiece).where(ContentPiece.content_brief_id == brief_row.id)))
        assert len(pieces) == 6  # 3 iterations x 2 successful creates each
        assert len({p.id for p in pieces}) == 6  # all distinct

        versions = list(
            check.scalars(select(ContentVersion).where(ContentVersion.content_piece_id.in_([p.id for p in pieces])))
        )
        assert len(versions) == 6
        assert len({v.id for v in versions}) == 6

        piece_events = list(
            check.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type == "content.piece.recorded",
                    AuditEvent.content_piece_id.in_([p.id for p in pieces]),
                )
            )
        )
        assert len(piece_events) == 6
        assert all(e.actor_type is ActorType.USER for e in piece_events)

        version_events = list(
            check.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type == "content.version.recorded",
                    AuditEvent.content_version_id.in_([v.id for v in versions]),
                )
            )
        )
        assert len(version_events) == 6
        assert all(e.actor_type is ActorType.USER for e in version_events)

    print(f"Piece concurrency iteration outcomes: {results}")
