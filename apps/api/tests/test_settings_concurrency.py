"""BACKEND-12 Freeze §15/§23/§38: a real, two-thread proof that the
singleton "first PATCH wins" upsert for UserPreference/AIPreference/
NotificationPreference is race-safe — two concurrent first-writers for
the same subject must never both succeed at inserting a row, must never
leak an unhandled ``IntegrityError`` to the caller, and must leave
exactly one row behind. Uses two independent connections against the
real test database (not the rollback-isolated `db_session` fixture,
which is a single connection and cannot demonstrate a real uniqueness
race — see ``tests/test_orchestration_concurrency.py`` for the same
established pattern applied to a row lock instead of a unique
constraint).
"""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.auth.security import hash_password
from app.users.models import User, UserPreference
from app.users.repository import UserRepository
from app.users.service import UserService
from app.workspaces.models import AIPreference, MembershipRole, NotificationPreference, Workspace
from app.workspaces.repository import MembershipRepository, OrganizationRepository, WorkspaceRepository
from app.workspaces.service import WorkspaceSettingsService

pytestmark = pytest.mark.postgres


def _run_concurrently(target_a, target_b) -> tuple[list[BaseException], list[BaseException]]:
    errors_a: list[BaseException] = []
    errors_b: list[BaseException] = []

    def _wrap(fn, errors):
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - captured for the assertion, not swallowed
            errors.append(exc)

    thread_a = threading.Thread(target=_wrap, args=(target_a, errors_a))
    thread_b = threading.Thread(target=_wrap, args=(target_b, errors_b))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=10)
    thread_b.join(timeout=10)
    return errors_a, errors_b


def test_concurrent_first_patch_never_creates_two_ai_preference_rows(postgres_engine) -> None:
    with postgres_engine.connect() as connection:
        session = OrmSession(bind=connection)
        organization = OrganizationRepository(session).create(name="Race Org")
        workspace = WorkspaceRepository(session).create(organization_id=organization.id, name="Race WS")
        user = UserRepository(session).create(
            email="race-ai@example.com", normalized_email="race-ai@example.com",
            password_hash=hash_password("x"), display_name="Race User",
        )
        MembershipRepository(session).create(user_id=user.id, workspace_id=workspace.id, role=MembershipRole.OWNER)
        session.commit()
        workspace_id, user_id = workspace.id, user.id
        session.close()

    barrier = threading.Barrier(2)

    def _patch(tone: str) -> None:
        with postgres_engine.connect() as connection:
            session = OrmSession(bind=connection)
            workspace = session.get(Workspace, workspace_id)
            barrier.wait(timeout=5)
            WorkspaceSettingsService(session).patch_settings(workspace=workspace, actor_user_id=user_id, tone=tone)

    errors_a, errors_b = _run_concurrently(lambda: _patch("direct"), lambda: _patch("educational"))

    assert errors_a == []
    assert errors_b == []
    with postgres_engine.connect() as connection:
        rows = connection.execute(select(AIPreference).where(AIPreference.workspace_id == workspace_id)).all()
        assert len(rows) == 1


def test_concurrent_first_patch_never_creates_two_notification_preference_rows(postgres_engine) -> None:
    with postgres_engine.connect() as connection:
        session = OrmSession(bind=connection)
        organization = OrganizationRepository(session).create(name="Race Org 2")
        workspace = WorkspaceRepository(session).create(organization_id=organization.id, name="Race WS 2")
        user = UserRepository(session).create(
            email="race-notif@example.com", normalized_email="race-notif@example.com",
            password_hash=hash_password("x"), display_name="Race User 2",
        )
        MembershipRepository(session).create(user_id=user.id, workspace_id=workspace.id, role=MembershipRole.OWNER)
        session.commit()
        workspace_id, user_id = workspace.id, user.id
        session.close()

    barrier = threading.Barrier(2)

    def _patch(value: bool) -> None:
        with postgres_engine.connect() as connection:
            session = OrmSession(bind=connection)
            workspace = session.get(Workspace, workspace_id)
            barrier.wait(timeout=5)
            WorkspaceSettingsService(session).patch_settings(
                workspace=workspace, actor_user_id=user_id, notifications={"campaign_ready": value}
            )

    errors_a, errors_b = _run_concurrently(lambda: _patch(False), lambda: _patch(True))

    assert errors_a == []
    assert errors_b == []
    with postgres_engine.connect() as connection:
        rows = connection.execute(
            select(NotificationPreference).where(
                NotificationPreference.workspace_id == workspace_id, NotificationPreference.user_id == user_id
            )
        ).all()
        assert len(rows) == 1


def test_concurrent_first_patch_never_creates_two_user_preference_rows(postgres_engine) -> None:
    with postgres_engine.connect() as connection:
        session = OrmSession(bind=connection)
        user = UserRepository(session).create(
            email="race-user@example.com", normalized_email="race-user@example.com",
            password_hash=hash_password("x"), display_name="Race User 3",
        )
        session.commit()
        user_id = user.id
        session.close()

    barrier = threading.Barrier(2)

    def _patch(locale: str) -> None:
        with postgres_engine.connect() as connection:
            session = OrmSession(bind=connection)
            user = session.get(User, user_id)
            barrier.wait(timeout=5)
            UserService(session).update_profile(user=user, locale=locale)

    errors_a, errors_b = _run_concurrently(lambda: _patch("es-VE"), lambda: _patch("en-US"))

    assert errors_a == []
    assert errors_b == []
    with postgres_engine.connect() as connection:
        rows = connection.execute(select(UserPreference).where(UserPreference.user_id == user_id)).all()
        assert len(rows) == 1
