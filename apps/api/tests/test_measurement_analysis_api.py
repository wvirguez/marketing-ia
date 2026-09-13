"""MVP-11C-B: HTTP tests for the public Measurement Analysis trigger —

    POST /api/v1/campaigns/{campaign_public_id}/analysis/run

Covers auth/CSRF/tenancy, request validation, the public response shape,
same-campaign idempotent replay (RUNNING/COMPLETED/FAILED), the
MVP-11C-A-R1 cross-campaign idempotency-key-collision repair (sequential
and genuinely concurrent), the router's own exception-translation
boundary, zero-metric behavior, and the Learning/Orchestration/GET
/analysis freeze. Real database required (``@pytest.mark.postgres``).

Two concurrency tests (closing Reservation 4 and proving the cross-campaign
resource invariant under a genuine race) call the actual route coroutine
``trigger_analysis_run`` directly from two threads, each bound to its own
PostgreSQL connection/session — mirroring
``tests/test_orchestration_concurrency.py``'s and
``tests/test_measurement_analysis_pipeline.py``'s own established
two-connection pattern, since a raw ``TestClient`` is not safe to drive
concurrently from multiple threads. This still exercises the production
route function itself (not a hand-copied reimplementation of its logic),
just without going through the ASGI/``Depends`` machinery — auth/CSRF/
tenancy dependency resolution is already separately and fully proven by
the ordinary HTTP-level tests in this same file.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session as OrmSession

from app.audit.models import AuditEvent
from app.campaigns.repository import CampaignRepository
from app.core.api_errors import IdempotencyKeyConflictError
from app.measurement.analysis_pipeline import EVENT_ANALYSIS_RUN_STARTED, MeasurementAnalysisService
from app.measurement.models import MeasurementAnalysisRun, MeasurementAnalysisRunStatus
from app.measurement.repository import MeasurementAnalysisRunRepository
from app.measurement.router import trigger_analysis_run
from app.measurement.schemas import MeasurementAnalysisRunTriggerRequest
from app.users.models import User
from app.users.repository import UserRepository
from app.workspaces.models import Workspace
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.measurementtest import next_client_request_id

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module", autouse=True)
def _reset_pipeline_derivation_tables(postgres_engine):
    """This module drives real, immediately-committing HTTP requests
    (never the rollback-isolated ``db_session`` fixture, since a real
    ``TestClient`` needs the app's own request-scoped session), so any
    Observation/Signal it creates via the analysis pipeline durably
    persists for the rest of this pytest session.
    ``tests/test_measurement_analysis_pipeline.py`` has two pre-existing
    tests that count every row in ``measurement_observation_derivations``/
    ``measurement_signal_derivations`` with no workspace/campaign filter —
    clear both tables here, after every test in this module has run, so
    this file's own real data can never affect that unrelated file's
    assertions regardless of file collection order. Safe with zero FK
    cascade concerns: nothing else references either table as a parent."""
    yield
    with postgres_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("DELETE FROM measurement_signal_derivations"))
            connection.execute(text("DELETE FROM measurement_observation_derivations"))


# =====================================================================
# Fixtures / helpers
# =====================================================================


@pytest.fixture()
def analysis_client(auth_client: TestClient) -> dict:
    """A freshly-registered, CSRF-armed client with one campaign already
    created — everything a trigger-route test needs, in the shape
    ``{"client", "csrf_token", "campaign_id"}``."""
    csrf_token = register_and_get_csrf(auth_client)
    body = auth_client.post(
        "/api/v1/campaigns", json=campaign_payload(), headers={"X-CSRF-Token": csrf_token}
    ).json()
    return {"client": auth_client, "csrf_token": csrf_token, "campaign_id": body["campaign"]["id"]}


def _run_path(campaign_public_id: str) -> str:
    return f"/api/v1/campaigns/{campaign_public_id}/analysis/run"


def _metrics_path(campaign_public_id: str) -> str:
    return f"/api/v1/campaigns/{campaign_public_id}/metrics"


_UNSET = object()


def _trigger(fixtures: dict, *, campaign_public_id: str | None = None, client_request_id: object = _UNSET):
    resolved_key = next_client_request_id() if client_request_id is _UNSET else client_request_id
    return fixtures["client"].post(
        _run_path(campaign_public_id or fixtures["campaign_id"]),
        json={"client_request_id": resolved_key},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )


def _record_metric(fixtures: dict, *, period_start: str, period_end: str, values: dict | None = None, channel: str = "Instagram") -> dict:
    values = values or {"clicks": "100"}
    response = fixtures["client"].post(
        _metrics_path(fixtures["campaign_id"]),
        json={
            "period_start": period_start,
            "period_end": period_end,
            "channel": channel,
            "source": "MANUAL",
            "client_request_id": next_client_request_id(),
            "values": values,
        },
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_second_campaign(fixtures: dict, *, name: str = "Second Campaign") -> str:
    body = fixtures["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name=name), headers={"X-CSRF-Token": fixtures["csrf_token"]}
    ).json()
    return body["campaign"]["id"]


def _invoke_route_sync(*, campaign_public_id: str, client_request_id: str, workspace: Workspace, user: User, session: OrmSession):
    """Directly calls the actual route coroutine with explicit dependency
    values (bypassing FastAPI's own ``Depends`` resolution, not the
    route's own logic) so two threads can drive the real production code
    path concurrently against two independent DB connections."""
    payload = MeasurementAnalysisRunTriggerRequest(client_request_id=client_request_id)
    request_stub = SimpleNamespace(state=SimpleNamespace(request_id="concurrency-test"))
    return asyncio.run(
        trigger_analysis_run(
            campaign_public_id=campaign_public_id,
            payload=payload,
            request=request_stub,
            workspace=workspace,
            user=user,
            db=session,
        )
    )


# =====================================================================
# AUTH (§35)
# =====================================================================


def test_unauthenticated_rejected(auth_client: TestClient) -> None:
    response = auth_client.post(_run_path("CMP-doesnotmatter00"), json={"client_request_id": next_client_request_id()})
    assert response.status_code == 401


def test_missing_csrf_rejected(analysis_client: dict) -> None:
    fixtures = analysis_client
    response = fixtures["client"].post(
        _run_path(fixtures["campaign_id"]), json={"client_request_id": next_client_request_id()}
    )
    assert response.status_code == 403


def test_invalid_csrf_rejected(analysis_client: dict) -> None:
    fixtures = analysis_client
    response = fixtures["client"].post(
        _run_path(fixtures["campaign_id"]),
        json={"client_request_id": next_client_request_id()},
        headers={"X-CSRF-Token": "not-the-real-token"},
    )
    assert response.status_code == 403


def test_valid_auth_and_csrf_accepted(analysis_client: dict) -> None:
    response = _trigger(analysis_client)
    assert response.status_code == 200


# =====================================================================
# TENANCY (§36)
# =====================================================================


def test_nonexistent_campaign_rejected(analysis_client: dict) -> None:
    response = _trigger(analysis_client, campaign_public_id="CMP-doesnotexist0000")
    assert response.status_code == 403


def test_campaign_in_another_workspace_rejected(analysis_client: dict, auth_client: TestClient) -> None:
    fixtures = analysis_client
    other_client = TestClient(auth_client.app, raise_server_exceptions=False)
    other_csrf = register_and_get_csrf(other_client, display_name="Outsider")
    response = other_client.post(
        _run_path(fixtures["campaign_id"]),
        json={"client_request_id": next_client_request_id()},
        headers={"X-CSRF-Token": other_csrf},
    )
    assert response.status_code == 403


# =====================================================================
# REQUEST VALIDATION (§37)
# =====================================================================


def test_missing_client_request_id_rejected(analysis_client: dict) -> None:
    fixtures = analysis_client
    response = fixtures["client"].post(
        _run_path(fixtures["campaign_id"]), json={}, headers={"X-CSRF-Token": fixtures["csrf_token"]}
    )
    assert response.status_code == 422


def test_empty_client_request_id_rejected(analysis_client: dict) -> None:
    response = _trigger(analysis_client, client_request_id="")
    assert response.status_code == 422


def test_oversized_client_request_id_rejected(analysis_client: dict) -> None:
    response = _trigger(analysis_client, client_request_id="x" * 101)
    assert response.status_code == 422


def test_opaque_non_uuid_client_request_id_accepted(analysis_client: dict) -> None:
    response = _trigger(analysis_client, client_request_id="not-a-uuid-just-an-opaque-string")
    assert response.status_code == 200


# =====================================================================
# PUBLIC DTO SHAPE (§38)
# =====================================================================


def test_response_public_shape_has_no_internal_ids(analysis_client: dict) -> None:
    fixtures = analysis_client
    response = _trigger(fixtures)
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "id", "campaign_id", "client_request_id", "status", "failure_reason", "created_at", "completed_at",
    }
    assert body["id"].startswith("MAR-")
    assert body["campaign_id"] == fixtures["campaign_id"]
    assert body["campaign_id"].startswith("CMP-")
    assert body["status"] == "COMPLETED"
    assert body["failure_reason"] is None
    for field in ("id", "campaign_id"):
        with pytest.raises(ValueError):
            uuid.UUID(body[field])  # a raw UUID would parse; a public_id must not


# =====================================================================
# ZERO-METRIC BEHAVIOR (§21)
# =====================================================================


def test_zero_metric_campaign_completes_with_no_evidence(analysis_client: dict) -> None:
    fixtures = analysis_client
    response = _trigger(fixtures)
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"

    analysis = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/analysis").json()
    assert analysis == {"observations": [], "signals": [], "analysis_results": []}


# =====================================================================
# SAME-CAMPAIGN IDEMPOTENCY (§26)
# =====================================================================


def test_same_campaign_replay_returns_same_run_without_re_execution(analysis_client: dict) -> None:
    fixtures = analysis_client
    _record_metric(fixtures, period_start="2026-01-01", period_end="2026-01-31")
    _record_metric(fixtures, period_start="2026-02-01", period_end="2026-02-28")
    key = next_client_request_id()

    first = _trigger(fixtures, client_request_id=key)
    second = _trigger(fixtures, client_request_id=key)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["campaign_id"] == fixtures["campaign_id"]
    assert second.json()["campaign_id"] == fixtures["campaign_id"]

    analysis_after_first = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/analysis").json()
    analysis_after_second = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/analysis").json()
    assert analysis_after_first == analysis_after_second
    assert len(analysis_after_first["observations"]) == 2
    assert len(analysis_after_first["signals"]) == 1


def test_same_campaign_completed_replay_returns_200(analysis_client: dict) -> None:
    response = _trigger(analysis_client)
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"


def test_same_campaign_running_replay_returns_running_without_mutation(analysis_client: dict, postgres_engine) -> None:
    fixtures = analysis_client
    key = next_client_request_id()

    with postgres_engine.connect() as connection:
        session = OrmSession(bind=connection)
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        stale_run = MeasurementAnalysisRunRepository(session).create(campaign=campaign, client_request_id=key)
        session.commit()
        stale_run_id = stale_run.id

    response = _trigger(fixtures, client_request_id=key)
    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"
    assert response.json()["campaign_id"] == fixtures["campaign_id"]

    with postgres_engine.connect() as connection:
        session = OrmSession(bind=connection)
        reloaded = session.get(MeasurementAnalysisRun, stale_run_id)
        assert reloaded.status is MeasurementAnalysisRunStatus.RUNNING, "a stale RUNNING run is never mutated by a replay"


def test_same_campaign_failed_replay_remains_failed(analysis_client: dict, monkeypatch) -> None:
    fixtures = analysis_client
    _record_metric(fixtures, period_start="2026-01-01", period_end="2026-01-31")
    key = next_client_request_id()

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated processing failure")

    monkeypatch.setattr(MeasurementAnalysisService, "_derive_signals", _boom)
    first = _trigger(fixtures, client_request_id=key)
    assert first.status_code == 200
    assert first.json()["status"] == "FAILED"

    monkeypatch.undo()  # replay must not re-execute even with the real code path restored
    second = _trigger(fixtures, client_request_id=key)
    assert second.status_code == 200
    assert second.json()["status"] == "FAILED"
    assert second.json()["id"] == first.json()["id"]


# =====================================================================
# FAILED-RUN TRANSLATION (§31)
# =====================================================================


def test_failed_run_translated_to_200_with_sanitized_reason(analysis_client: dict, monkeypatch) -> None:
    fixtures = analysis_client
    _record_metric(fixtures, period_start="2026-01-01", period_end="2026-01-31")

    def _boom(*args, **kwargs):
        raise RuntimeError("raw internal exception text that must never reach the client")

    monkeypatch.setattr(MeasurementAnalysisService, "_derive_signals", _boom)
    response = _trigger(fixtures)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["campaign_id"] == fixtures["campaign_id"]
    assert body["failure_reason"]
    response_text = response.text
    for forbidden in ("Traceback", "RuntimeError", "raw internal exception"):
        assert forbidden not in response_text


# =====================================================================
# NO-RUN INTERNAL FAILURE (§32)
# =====================================================================


def test_no_run_persists_when_snapshot_fails(analysis_client: dict, monkeypatch, postgres_engine) -> None:
    fixtures = analysis_client
    key = next_client_request_id()

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated snapshot failure")

    monkeypatch.setattr(MeasurementAnalysisService, "_select_current_metric_entries", _boom)
    response = _trigger(fixtures, client_request_id=key)
    assert response.status_code == 500

    with postgres_engine.connect() as connection:
        rows = connection.execute(
            select(MeasurementAnalysisRun.__table__).where(MeasurementAnalysisRun.__table__.c.client_request_id == key)
        ).all()
        assert rows == [], "no MeasurementAnalysisRun may persist for a snapshot-phase failure"


# =====================================================================
# CROSS-CAMPAIGN COLLISION — sequential (§28)
# =====================================================================


def test_cross_campaign_sequential_key_collision_returns_409(analysis_client: dict, postgres_engine) -> None:
    fixtures = analysis_client
    key = next_client_request_id()

    first = _trigger(fixtures, client_request_id=key)
    assert first.status_code == 200
    campaign_a_run_id = first.json()["id"]

    campaign_b_id = _create_second_campaign(fixtures, name="Collision Campaign B")
    second = _trigger(fixtures, campaign_public_id=campaign_b_id, client_request_id=key)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert "id" not in second.json()
    assert campaign_a_run_id not in second.text

    # Campaign B created no run of its own for this key.
    replay_a = _trigger(fixtures, client_request_id=key)
    assert replay_a.status_code == 200
    assert replay_a.json()["id"] == campaign_a_run_id

    with postgres_engine.connect() as connection:
        rows = connection.execute(
            select(MeasurementAnalysisRun.__table__).where(MeasurementAnalysisRun.__table__.c.client_request_id == key)
        ).all()
        assert len(rows) == 1, "exactly one run must exist for this key across both campaigns"


# =====================================================================
# EXCEPTION + OTHER-CAMPAIGN RUN (§33)
# =====================================================================


def test_exception_with_existing_other_campaign_run_returns_409(analysis_client: dict, monkeypatch) -> None:
    fixtures = analysis_client
    key = next_client_request_id()

    first = _trigger(fixtures, client_request_id=key)
    assert first.status_code == 200
    campaign_a_id = fixtures["campaign_id"]
    campaign_a_run_id = first.json()["id"]

    campaign_b_id = _create_second_campaign(fixtures, name="Exception Collision Campaign B")

    def _boom(*args, **kwargs):
        raise RuntimeError("forced to exercise the router's exception-recovery branch")

    monkeypatch.setattr(MeasurementAnalysisService, "run_analysis", _boom)
    response = _trigger(fixtures, campaign_public_id=campaign_b_id, client_request_id=key)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert campaign_a_id not in response.text
    assert campaign_a_run_id not in response.text


def test_unexpected_completed_run_after_exception_is_not_masked_as_success(analysis_client: dict, monkeypatch) -> None:
    fixtures = analysis_client
    key = next_client_request_id()

    first = _trigger(fixtures, client_request_id=key)
    assert first.status_code == 200
    assert first.json()["status"] == "COMPLETED"

    def _boom(*args, **kwargs):
        raise RuntimeError("forced failure despite an already-COMPLETED same-campaign run existing for this key")

    monkeypatch.setattr(MeasurementAnalysisService, "run_analysis", _boom)
    response = _trigger(fixtures, client_request_id=key)
    assert response.status_code == 500


# =====================================================================
# CONCURRENCY — same campaign (§27, closes Reservation 4)
# =====================================================================


def test_concurrent_same_campaign_identical_key_resolves_to_one_run(postgres_engine) -> None:
    with postgres_engine.connect() as setup_connection:
        setup_session = OrmSession(bind=setup_connection)
        organization = OrganizationRepository(setup_session).create(name="Same-Campaign Concurrency Org")
        workspace = WorkspaceRepository(setup_session).create(
            organization_id=organization.id, name="Same-Campaign Concurrency WS"
        )
        user = UserRepository(setup_session).create(
            email="same-campaign-concurrency@example.com",
            normalized_email="same-campaign-concurrency@example.com",
            password_hash="x",
            display_name="Concurrency User",
        )
        campaign = CampaignRepository(setup_session).create(
            workspace_id=workspace.id, name="Same-Campaign Concurrency Campaign"
        )
        setup_session.commit()
        workspace_id, user_id = workspace.id, user.id
        campaign_public_id = campaign.public_id

    key = next_client_request_id()
    ready = threading.Barrier(2)
    results: list = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def attempt() -> None:
        with postgres_engine.connect() as connection:
            session = OrmSession(bind=connection)
            try:
                local_workspace = session.get(Workspace, workspace_id)
                local_user = session.get(User, user_id)
                ready.wait(timeout=5)
                result = _invoke_route_sync(
                    campaign_public_id=campaign_public_id,
                    client_request_id=key,
                    workspace=local_workspace,
                    user=local_user,
                    session=session,
                )
                with lock:
                    results.append(result)
            except Exception as exc:  # pragma: no cover - surfaced via errors list
                with lock:
                    errors.append(exc)
            finally:
                session.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, f"unexpected errors: {errors}"
    assert len(results) == 2
    assert results[0].id == results[1].id
    assert results[0].campaign_id == campaign_public_id
    assert results[1].campaign_id == campaign_public_id

    with postgres_engine.connect() as connection:
        rows = connection.execute(
            select(MeasurementAnalysisRun.__table__).where(MeasurementAnalysisRun.__table__.c.client_request_id == key)
        ).all()
        assert len(rows) == 1, "exactly one run must exist despite the concurrent identical-key race"


# =====================================================================
# CONCURRENCY — cross campaign (§29, proves the resource invariant)
# =====================================================================


def test_concurrent_cross_campaign_identical_key_yields_one_200_one_409(postgres_engine) -> None:
    with postgres_engine.connect() as setup_connection:
        setup_session = OrmSession(bind=setup_connection)
        organization = OrganizationRepository(setup_session).create(name="Cross-Campaign Concurrency Org")
        workspace = WorkspaceRepository(setup_session).create(
            organization_id=organization.id, name="Cross-Campaign Concurrency WS"
        )
        user = UserRepository(setup_session).create(
            email="cross-campaign-concurrency@example.com",
            normalized_email="cross-campaign-concurrency@example.com",
            password_hash="x",
            display_name="Concurrency User",
        )
        campaign_a = CampaignRepository(setup_session).create(
            workspace_id=workspace.id, name="Cross-Campaign Concurrency A"
        )
        campaign_b = CampaignRepository(setup_session).create(
            workspace_id=workspace.id, name="Cross-Campaign Concurrency B"
        )
        setup_session.commit()
        workspace_id, user_id = workspace.id, user.id
        campaign_a_id, campaign_b_id = campaign_a.id, campaign_b.id
        campaign_a_public_id, campaign_b_public_id = campaign_a.public_id, campaign_b.public_id

    key = next_client_request_id()
    ready = threading.Barrier(2)
    outcomes: dict[str, tuple] = {}
    errors: list[Exception] = []
    lock = threading.Lock()

    def attempt(label: str, campaign_public_id: str) -> None:
        with postgres_engine.connect() as connection:
            session = OrmSession(bind=connection)
            try:
                local_workspace = session.get(Workspace, workspace_id)
                local_user = session.get(User, user_id)
                ready.wait(timeout=5)
                try:
                    result = _invoke_route_sync(
                        campaign_public_id=campaign_public_id,
                        client_request_id=key,
                        workspace=local_workspace,
                        user=local_user,
                        session=session,
                    )
                    with lock:
                        outcomes[label] = ("200", result)
                except IdempotencyKeyConflictError as exc:
                    with lock:
                        outcomes[label] = ("409", exc)
            except Exception as exc:  # pragma: no cover - surfaced via errors list
                with lock:
                    errors.append(exc)
            finally:
                session.close()

    threads = [
        threading.Thread(target=attempt, args=("A", campaign_a_public_id)),
        threading.Thread(target=attempt, args=("B", campaign_b_public_id)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, f"unexpected non-conflict errors: {errors}"
    assert len(outcomes) == 2
    statuses = sorted(status for status, _ in outcomes.values())
    assert statuses == ["200", "409"], f"expected exactly one 200 and one 409, got {outcomes}"

    winner_label = "A" if outcomes["A"][0] == "200" else "B"
    winner_public_id = campaign_a_public_id if winner_label == "A" else campaign_b_public_id
    winner_internal_id = campaign_a_id if winner_label == "A" else campaign_b_id
    winning_result = outcomes[winner_label][1]
    assert winning_result.campaign_id == winner_public_id

    loser_label = "B" if winner_label == "A" else "A"
    loser_error = outcomes[loser_label][1]
    assert isinstance(loser_error, IdempotencyKeyConflictError)
    assert loser_error.status_code == 409
    assert winning_result.id not in loser_error.message
    assert winner_public_id not in loser_error.message

    with postgres_engine.connect() as connection:
        rows = connection.execute(
            select(MeasurementAnalysisRun.__table__).where(MeasurementAnalysisRun.__table__.c.client_request_id == key)
        ).all()
        assert len(rows) == 1, "exactly one run total must exist across both racing campaigns"
        assert rows[0].campaign_id == winner_internal_id


# =====================================================================
# RESOURCE INVARIANT — every 2xx response (§30)
# =====================================================================


def test_resource_invariant_holds_across_every_2xx_path(analysis_client: dict, postgres_engine) -> None:
    fixtures = analysis_client
    _record_metric(fixtures, period_start="2026-01-01", period_end="2026-01-31")
    _record_metric(fixtures, period_start="2026-02-01", period_end="2026-02-28")

    # first execution
    key_first = next_client_request_id()
    first = _trigger(fixtures, client_request_id=key_first)
    assert first.status_code == 200
    assert first.json()["campaign_id"] == fixtures["campaign_id"]

    # COMPLETED replay
    replay = _trigger(fixtures, client_request_id=key_first)
    assert replay.status_code == 200
    assert replay.json()["campaign_id"] == fixtures["campaign_id"]

    # zero-metric execution on a second, otherwise-empty campaign
    empty_campaign_id = _create_second_campaign(fixtures, name="Invariant Empty Campaign")
    empty_response = _trigger(fixtures, campaign_public_id=empty_campaign_id, client_request_id=next_client_request_id())
    assert empty_response.status_code == 200
    assert empty_response.json()["campaign_id"] == empty_campaign_id

    # RUNNING replay
    running_key = next_client_request_id()
    with postgres_engine.connect() as connection:
        session = OrmSession(bind=connection)
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        MeasurementAnalysisRunRepository(session).create(campaign=campaign, client_request_id=running_key)
        session.commit()
    running_response = _trigger(fixtures, client_request_id=running_key)
    assert running_response.status_code == 200
    assert running_response.json()["status"] == "RUNNING"
    assert running_response.json()["campaign_id"] == fixtures["campaign_id"]


# =====================================================================
# GET /analysis FREEZE (§22)
# =====================================================================


def test_get_analysis_response_shape_unchanged_after_trigger(analysis_client: dict) -> None:
    fixtures = analysis_client
    _record_metric(fixtures, period_start="2026-01-01", period_end="2026-01-31")
    _record_metric(fixtures, period_start="2026-02-01", period_end="2026-02-28")
    _trigger(fixtures)

    response = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/analysis")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"observations", "signals", "analysis_results"}
    assert len(body["observations"]) == 2
    assert len(body["signals"]) == 1
    for observation in body["observations"]:
        assert set(observation.keys()) == {"id", "metric_name", "value", "source_metric_entry_ids", "created_at"}


# =====================================================================
# LEARNING FREEZE (§24)
# =====================================================================


def test_no_learning_writes(analysis_client: dict) -> None:
    fixtures = analysis_client
    _record_metric(fixtures, period_start="2026-01-01", period_end="2026-01-31")
    _record_metric(fixtures, period_start="2026-02-01", period_end="2026-02-28")
    _trigger(fixtures)

    learning = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/learning").json()
    assert learning["learning_candidates"] == []
    assert learning["strategic_recommendation_candidates"] == []


# =====================================================================
# ORCHESTRATION FREEZE (§25)
# =====================================================================


def test_no_campaign_run_mutation(analysis_client: dict) -> None:
    fixtures = analysis_client
    before = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/runs").json()
    _trigger(fixtures)
    after = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/runs").json()
    assert before == after


# =====================================================================
# ACTOR / REQUEST TRACE (§41)
# =====================================================================


def test_actor_and_request_trace_propagate_to_audit_events(analysis_client: dict, postgres_engine) -> None:
    fixtures = analysis_client
    response = _trigger(fixtures)
    assert response.status_code == 200
    request_id_header = response.headers.get("X-Request-ID")
    assert request_id_header

    with postgres_engine.connect() as connection:
        session = OrmSession(bind=connection)
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        started_event = session.execute(
            select(AuditEvent).where(
                AuditEvent.campaign_id == campaign.id, AuditEvent.event_type == EVENT_ANALYSIS_RUN_STARTED
            )
        ).scalars().first()
        assert started_event is not None
        assert started_event.actor_user_id is not None
        assert started_event.request_id == request_id_header


# =====================================================================
# API ERROR MAPPING (§40)
# =====================================================================


def test_idempotency_key_conflict_error_shape() -> None:
    error = IdempotencyKeyConflictError()
    assert error.status_code == 409
    assert error.code == "IDEMPOTENCY_KEY_CONFLICT"
    assert "campaign" not in error.message.lower()
    assert "workspace" not in error.message.lower()
