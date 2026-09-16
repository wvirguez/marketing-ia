"""MVP-17A-R1 / MVP-20A-R1 concurrency repairs: real, two-thread proofs
through the production ``ContentService`` methods. Uses two independent
connections against the real test database (mirrors
``tests/test_tracking_concurrency.py``'s/``tests/test_settings_concurrency.py``'s
established two-thread/barrier pattern), since the single-connection,
rollback-isolated ``db_session`` fixture cannot demonstrate a real row-lock
race.

Covers, per MVP-20B §30-§35:

- request_approval vs request_approval (MVP-17A-R1, preserved unchanged
  under the refactored signature)
- create_revision_version vs create_revision_version (version/version)
- create_revision_version vs mark_in_production (CONTENT-P0-6 closure)
- create_revision_version vs request_approval (CONTENT-P3-4 closure)
- request_approval vs archive_piece (CONTENT-P3-5 closure — status
  staleness)
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.content.models import ContentApproval, ContentApprovalStatus, ContentPiece, ContentPieceStatus, ContentVersion
from app.content.service import ContentService
from app.core.api_errors import ContentApprovalAlreadyOpenError, InvalidLifecycleTransitionError
from tests.contenttest import build_plan_with_item, default_piece_fields, default_version_payload, make_user

pytestmark = pytest.mark.postgres


def _distinct_pids_barrier(backend_pids: list, pid_lock: threading.Lock) -> threading.Barrier:
    def verify_distinct_connections() -> None:
        assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1], backend_pids

    return threading.Barrier(2, action=verify_distinct_connections)


def _ready_for_review_piece(postgres_engine) -> tuple[str, object]:
    """Returns (piece_public_id, workspace_id) for a fresh Piece already
    brought to READY_FOR_REVIEW with its initial Version V1."""
    with postgres_engine.connect() as connection:
        session = OrmSession(bind=connection)
        campaign, _run, _stages, plan, item = build_plan_with_item(session)
        service = ContentService(session)
        brief = service.record_brief(plan_item=item, content_plan=plan, brief="Concurrency race.")
        piece, _v1 = service.record_piece(
            content_brief=brief, initial_payload=default_version_payload(), **default_piece_fields()
        )
        service.mark_in_production(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
        service.mark_produced(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
        service.mark_ready_for_review(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
        session.commit()
        public_id, workspace_id = piece.public_id, campaign.workspace_id
        session.close()
    return public_id, workspace_id


def _revision_requested_piece(postgres_engine) -> tuple[str, object, str]:
    """Returns (piece_public_id, workspace_id, v1_public_id) for a Piece
    that has already gone through one full CHANGES_REQUESTED cycle and is
    sitting at REVISION_REQUESTED."""
    with postgres_engine.connect() as connection:
        session = OrmSession(bind=connection)
        campaign, _run, _stages, plan, item = build_plan_with_item(session)
        service = ContentService(session)
        brief = service.record_brief(plan_item=item, content_plan=plan, brief="Concurrency race.")
        piece, v1 = service.record_piece(
            content_brief=brief, initial_payload=default_version_payload(), **default_piece_fields()
        )
        service.mark_in_production(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
        service.mark_produced(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
        service.mark_ready_for_review(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
        approval = service.request_approval(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id)
        service.mark_under_review(workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id)
        reviewer = make_user(session)
        service.record_authorized_approval_decision(
            workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id,
            decision=ContentApprovalStatus.CHANGES_REQUESTED, actor_user_id=reviewer.id,
        )
        session.commit()
        public_id, workspace_id, v1_public_id = piece.public_id, campaign.workspace_id, v1.public_id
        session.close()
    return public_id, workspace_id, v1_public_id


# --- 1. request_approval vs request_approval (MVP-17A-R1, preserved) -------


def test_concurrent_request_approval_yields_exactly_one_winner_and_a_clean_conflict(postgres_engine) -> None:
    public_id, workspace_id = _ready_for_review_piece(postgres_engine)

    backend_pids: list = []
    pid_lock = threading.Lock()
    barrier = _distinct_pids_barrier(backend_pids, pid_lock)
    results: dict[str, tuple[str, object]] = {}

    def _request(key: str) -> None:
        with postgres_engine.connect() as connection:
            pid = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
            connection.commit()
            with pid_lock:
                backend_pids.append(pid)
            session = OrmSession(bind=connection)
            barrier.wait(timeout=10)
            try:
                approval = ContentService(session).request_approval(
                    workspace_id=workspace_id, content_piece_public_id=public_id
                )
                results[key] = ("ok", approval.id)
            except ContentApprovalAlreadyOpenError as exc:
                results[key] = ("conflict", exc)

    thread_a = threading.Thread(target=_request, args=("a",))
    thread_b = threading.Thread(target=_request, args=("b",))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=20)
    thread_b.join(timeout=20)

    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1], backend_pids
    assert set(results.keys()) == {"a", "b"}, f"a thread never completed: {results}"
    outcomes = {results["a"][0], results["b"][0]}
    assert outcomes == {"ok", "conflict"}, f"expected exactly one winner and one conflict, got {results}"

    with postgres_engine.connect() as connection:
        session = OrmSession(bind=connection)
        piece = session.execute(select(ContentPiece).where(ContentPiece.public_id == public_id)).scalar_one()
        approvals = session.execute(select(ContentApproval).where(ContentApproval.content_version_id.in_(
            select(ContentVersion.id).where(ContentVersion.content_piece_id == piece.id)
        ))).scalars().all()
        assert len(approvals) == 1, "exactly one ContentApproval must survive the race"
        assert approvals[0].status is ContentApprovalStatus.REQUESTED


# --- 2. create_revision_version vs create_revision_version (version/version) ---


def test_concurrent_create_revision_version_yields_one_v2_and_a_clean_conflict(postgres_engine) -> None:
    public_id, workspace_id, v1_public_id = _revision_requested_piece(postgres_engine)

    backend_pids: list = []
    pid_lock = threading.Lock()
    barrier = _distinct_pids_barrier(backend_pids, pid_lock)
    results: dict[str, tuple[str, object]] = {}

    def _create(key: str) -> None:
        with postgres_engine.connect() as connection:
            pid = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
            connection.commit()
            with pid_lock:
                backend_pids.append(pid)
            session = OrmSession(bind=connection)
            barrier.wait(timeout=10)
            try:
                version = ContentService(session).create_revision_version(
                    workspace_id=workspace_id, content_piece_public_id=public_id,
                    payload=default_version_payload(hook=f"revision from {key}"),
                )
                results[key] = ("ok", version.id)
            except InvalidLifecycleTransitionError as exc:
                results[key] = ("conflict", exc)

    thread_a = threading.Thread(target=_create, args=("a",))
    thread_b = threading.Thread(target=_create, args=("b",))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=20)
    thread_b.join(timeout=20)

    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1], backend_pids
    outcomes = {results["a"][0], results["b"][0]}
    assert outcomes == {"ok", "conflict"}, f"expected exactly one winner and one conflict, got {results}"

    with postgres_engine.connect() as connection:
        session = OrmSession(bind=connection)
        piece = session.execute(select(ContentPiece).where(ContentPiece.public_id == public_id)).scalar_one()
        versions = session.execute(select(ContentVersion).where(ContentVersion.content_piece_id == piece.id)).scalars().all()
        assert len(versions) == 2, f"expected exactly V1+V2, got {[v.public_id for v in versions]}"
        assert v1_public_id in {v.public_id for v in versions}
        assert piece.status is ContentPieceStatus.IN_PRODUCTION


# --- 3. create_revision_version vs mark_in_production (CONTENT-P0-6) -------


def test_create_revision_version_vs_mark_in_production_only_version_creation_succeeds(postgres_engine) -> None:
    public_id, workspace_id, v1_public_id = _revision_requested_piece(postgres_engine)

    backend_pids: list = []
    pid_lock = threading.Lock()
    barrier = _distinct_pids_barrier(backend_pids, pid_lock)
    results: dict[str, str] = {}

    def _run(key: str, action) -> None:
        with postgres_engine.connect() as connection:
            pid = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
            connection.commit()
            with pid_lock:
                backend_pids.append(pid)
            session = OrmSession(bind=connection)
            barrier.wait(timeout=10)
            try:
                action(ContentService(session))
                results[key] = "ok"
            except InvalidLifecycleTransitionError:
                results[key] = "conflict"

    thread_version = threading.Thread(
        target=_run, args=(
            "version",
            lambda service: service.create_revision_version(
                workspace_id=workspace_id, content_piece_public_id=public_id, payload=default_version_payload()
            ),
        ),
    )
    thread_bypass = threading.Thread(
        target=_run, args=(
            "bypass",
            lambda service: service.mark_in_production(workspace_id=workspace_id, content_piece_public_id=public_id),
        ),
    )
    thread_version.start()
    thread_bypass.start()
    thread_version.join(timeout=20)
    thread_bypass.join(timeout=20)

    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1], backend_pids
    assert results["version"] == "ok", results
    assert results["bypass"] == "conflict", "the generic mark_in_production bypass must never succeed from REVISION_REQUESTED"

    with postgres_engine.connect() as connection:
        session = OrmSession(bind=connection)
        piece = session.execute(select(ContentPiece).where(ContentPiece.public_id == public_id)).scalar_one()
        versions = session.execute(select(ContentVersion).where(ContentVersion.content_piece_id == piece.id)).scalars().all()
        assert len(versions) == 2, "exactly one new Version — through the version-creating command only"
        assert piece.status is ContentPieceStatus.IN_PRODUCTION


# --- 4. create_revision_version vs request_approval (CONTENT-P3-4) ---------


def test_create_revision_version_vs_request_approval_no_stale_version_approval(postgres_engine) -> None:
    """create_revision_version requires REVISION_REQUESTED;
    request_approval requires READY_FOR_REVIEW — disjoint preconditions on
    the same Piece lock. Starting genuinely at REVISION_REQUESTED, only
    the version-creating side can ever legally proceed; request_approval
    must fail cleanly regardless of lock-acquisition order, and in
    particular can never reference a stale prior Version."""
    public_id, workspace_id, v1_public_id = _revision_requested_piece(postgres_engine)

    backend_pids: list = []
    pid_lock = threading.Lock()
    barrier = _distinct_pids_barrier(backend_pids, pid_lock)
    results: dict[str, str] = {}

    def _version(key: str) -> None:
        with postgres_engine.connect() as connection:
            pid = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
            connection.commit()
            with pid_lock:
                backend_pids.append(pid)
            session = OrmSession(bind=connection)
            barrier.wait(timeout=10)
            ContentService(session).create_revision_version(
                workspace_id=workspace_id, content_piece_public_id=public_id, payload=default_version_payload()
            )
            results[key] = "ok"

    def _approval(key: str) -> None:
        with postgres_engine.connect() as connection:
            pid = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
            connection.commit()
            with pid_lock:
                backend_pids.append(pid)
            session = OrmSession(bind=connection)
            barrier.wait(timeout=10)
            try:
                ContentService(session).request_approval(workspace_id=workspace_id, content_piece_public_id=public_id)
                results[key] = "ok"
            except InvalidLifecycleTransitionError:
                results[key] = "conflict"

    thread_version = threading.Thread(target=_version, args=("version",))
    thread_approval = threading.Thread(target=_approval, args=("approval",))
    thread_version.start()
    thread_approval.start()
    thread_version.join(timeout=20)
    thread_approval.join(timeout=20)

    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1], backend_pids
    assert results["version"] == "ok"
    assert results["approval"] == "conflict", "request_approval must never succeed while the Piece is REVISION_REQUESTED"

    with postgres_engine.connect() as connection:
        session = OrmSession(bind=connection)
        piece = session.execute(select(ContentPiece).where(ContentPiece.public_id == public_id)).scalar_one()
        v1 = session.execute(select(ContentVersion).where(ContentVersion.public_id == v1_public_id)).scalar_one()
        approvals = session.execute(select(ContentApproval).where(ContentApproval.content_version_id == v1.id)).scalars().all()
        # Exactly the one pre-existing A1 (CHANGES_REQUESTED, created by
        # the setup fixture) — no *second/new* Approval was ever created
        # against the stale V1 through this race.
        assert len(approvals) == 1, "no new Approval may ever be created against the stale V1 through this race"
        assert approvals[0].status is ContentApprovalStatus.CHANGES_REQUESTED
        assert piece.status is ContentPieceStatus.IN_PRODUCTION


# --- 5. request_approval vs archive_piece (CONTENT-P3-5, status staleness) -


def test_request_approval_vs_archive_piece_no_approval_survives_a_stale_status_read(postgres_engine) -> None:
    """CONTENT-P3-5 closure proof: request_approval now re-checks
    READY_FOR_REVIEW under its own Piece lock, never trusting a pre-lock
    read. Racing it against archive_piece (which also locks the Piece,
    and does not itself check for an open Approval) must never let
    request_approval succeed based on a status that was already stale by
    the time its lock was granted — whichever operation's lock is granted
    first fully determines a consistent outcome for the other."""
    public_id, workspace_id = _ready_for_review_piece(postgres_engine)

    backend_pids: list = []
    pid_lock = threading.Lock()
    barrier = _distinct_pids_barrier(backend_pids, pid_lock)
    results: dict[str, str] = {}

    def _request_approval(key: str) -> None:
        with postgres_engine.connect() as connection:
            pid = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
            connection.commit()
            with pid_lock:
                backend_pids.append(pid)
            session = OrmSession(bind=connection)
            barrier.wait(timeout=10)
            try:
                ContentService(session).request_approval(workspace_id=workspace_id, content_piece_public_id=public_id)
                results[key] = "ok"
            except InvalidLifecycleTransitionError:
                results[key] = "conflict"

    def _archive(key: str) -> None:
        with postgres_engine.connect() as connection:
            pid = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
            connection.commit()
            with pid_lock:
                backend_pids.append(pid)
            session = OrmSession(bind=connection)
            barrier.wait(timeout=10)
            ContentService(session).archive_piece(workspace_id=workspace_id, content_piece_public_id=public_id)
            results[key] = "ok"

    thread_approval = threading.Thread(target=_request_approval, args=("approval",))
    thread_archive = threading.Thread(target=_archive, args=("archive",))
    thread_approval.start()
    thread_archive.start()
    thread_approval.join(timeout=20)
    thread_archive.join(timeout=20)

    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1], backend_pids
    assert results["archive"] == "ok", "archive_piece is legal from READY_FOR_REVIEW regardless of race order"

    with postgres_engine.connect() as connection:
        session = OrmSession(bind=connection)
        piece = session.execute(select(ContentPiece).where(ContentPiece.public_id == public_id)).scalar_one()
        approvals = session.execute(select(ContentApproval).where(ContentApproval.content_version_id.in_(
            select(ContentVersion.id).where(ContentVersion.content_piece_id == piece.id)
        ))).scalars().all()
        assert piece.status is ContentPieceStatus.ARCHIVED

        if results["approval"] == "ok":
            # request_approval's own lock was granted first, while the
            # Piece was genuinely still READY_FOR_REVIEW — a legitimate
            # Approval, not a stale one, and archive_piece correctly
            # proceeded afterward regardless (it never checks for an
            # open Approval).
            assert len(approvals) == 1
            assert approvals[0].status is ContentApprovalStatus.REQUESTED
        else:
            # archive_piece's lock was granted first — request_approval's
            # post-lock re-check correctly observed ARCHIVED and refused,
            # so no Approval was ever created. This is the exact scenario
            # CONTENT-P3-5 closes: no approval survives a stale read.
            assert len(approvals) == 0
