"""MVP25 real PostgreSQL HTTP governance and historical-integrity acceptance."""
from app.core.ids import generate_public_id
import pytest
from sqlalchemy import select, func, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.persistence.session import get_engine
from app.learning.models import LearningCandidate, LearningCandidateStatus, LearningQualification, LearningQualificationSignal
from app.measurement.models import PerformanceSignal, AnalysisResultSignal
from app.audit.models import AuditEvent, ActorType
from app.campaigns.repository import CampaignRepository
from tests.test_learning_maturation_api import _build_candidate, _path, _post

pytestmark = pytest.mark.postgres
SUFFICIENT = {"confidence": "LOW", "scope": "Only this audience and period", "generalization_boundary": "No broader or causal inference"}

@pytest.fixture
def subject(campaign_run_client):
    f = campaign_run_client
    cid = _build_candidate(f["campaign_id"], target_status=LearningCandidateStatus.VALIDATION_PENDING, qualified=False)
    return f, cid

def patch(f, cid, values):
    return f["client"].patch(_path(f, cid, "qualification"), json=values, headers={"X-CSRF-Token": f["csrf_token"]})

def signal(f):
    with Session(get_engine()) as s:
        c = CampaignRepository(s).get_by_public_id(f["campaign_id"])
        row = PerformanceSignal(public_id=generate_public_id("SIG"), workspace_id=c.workspace_id, campaign_id=c.id, summary="Human observed additional signal")
        s.add(row); s.commit()
        return row.public_id

def attach(f, cid, sid, relationship="CONTRADICTING", **extra):
    return _post(f, _path(f, cid, "qualification/signals"), {"performance_signal_id": sid, "relationship": relationship, "note": "Different observed direction", **extra})

def dispose(f, cid, sid, reason, **extra):
    return _post(f, _path(f, cid, f"qualification/signals/{sid}/dispose"), {"removal_reason": reason, "removal_note": "Human correction rationale", **extra})

def validate(f, cid):
    return _post(f, _path(f, cid, "decision"), {"decision": "VALIDATED"})

@pytest.mark.parametrize("confidence", ["LOW", "MEDIUM", "HIGH"])
def test_lazy_qualification_and_bounded_validation(subject, confidence):
    f, cid = subject
    assert validate(f, cid).status_code == 409
    result = patch(f, cid, {**SUFFICIENT, "confidence": confidence})
    assert result.status_code == 200, result.text
    q = result.json()["qualification"]
    assert q["consistency_status"] == "NOT_ASSESSED"
    assert q["replication_status"] == "REPLICATION_NOT_ESTABLISHED"
    assert not q["validation_blockers"]
    assert validate(f, cid).status_code == 200
    # MVP-26: a new Recommendation now requires a StrategicImplication,
    # which itself requires the same sufficiency this qualification just
    # satisfied.
    implication = _post(f, _path(f, cid, "strategic-implications"), {"statement": "Bounded implication"})
    assert implication.status_code == 201, implication.text
    assert _post(
        f, _path(f, cid, "recommendations"),
        {"strategic_implication_id": implication.json()["id"], "summary": "Bounded recommendation"},
    ).status_code == 201

@pytest.mark.parametrize("missing", ["confidence", "scope", "generalization_boundary"])
def test_required_qualification_fields(subject, missing):
    f, cid = subject
    assert patch(f, cid, {**SUFFICIENT, missing: None}).status_code == 200
    assert validate(f, cid).status_code == 409
    if missing != "confidence":
        assert patch(f, cid, {missing: " \t\n "}).status_code == 200
        assert validate(f, cid).status_code == 409

@pytest.mark.parametrize("reason", ["ATTACHMENT_ERROR", "OTHER"])
def test_negative_history_and_reattachment(subject, reason):
    f, cid = subject
    assert patch(f, cid, SUFFICIENT).status_code == 200
    sid = signal(f)
    assert attach(f, cid, sid).status_code == 201
    assert validate(f, cid).status_code == 409
    assert patch(f, cid, {"generalization_boundary": "Narrative and newer timestamp cannot resolve contradiction"}).status_code == 200
    assert validate(f, cid).status_code == 409
    r = dispose(f, cid, sid, reason)
    assert r.status_code == 200, r.text
    q = r.json()["qualification"]; row = q["evidence"][0]
    assert row["removal_reason"] == reason and row["removed_at"] and row["removed_by_user_id"].startswith("USR-")
    assert row["blocks_validation"] == (reason == "OTHER")
    assert row["governance_effective"] == (reason == "OTHER")
    assert q["consistency_status"] == ("CONTRADICTING" if reason == "OTHER" else "NOT_ASSESSED")
    assert attach(f, cid, sid, "SUPPORTING").status_code == (409 if reason == "OTHER" else 201)
    with Session(get_engine()) as s:
        candidate = s.scalar(select(LearningCandidate).where(LearningCandidate.public_id == cid))
        qrow = s.scalar(select(LearningQualification).where(LearningQualification.learning_candidate_id == candidate.id))
        rows = list(s.scalars(select(LearningQualificationSignal).where(LearningQualificationSignal.learning_qualification_id == qrow.id)))
        assert len(rows) == (1 if reason == "OTHER" else 2)
        events = list(s.scalars(select(AuditEvent).where(AuditEvent.learning_candidate_id == candidate.id, AuditEvent.event_type.like("learning.qualification.%"))))
        assert all(e.actor_type == ActorType.USER and e.actor_user_id for e in events)
        assert len([e for e in events if e.event_type.endswith("signal_dispositioned")]) == 1
    assert validate(f, cid).status_code == (409 if reason == "OTHER" else 200)


def test_replication_coherence_is_atomic_and_derived(subject):
    f, cid = subject
    assert patch(f, cid, {**SUFFICIENT, "replication_status": "REPLICATION_EVIDENCE_PRESENT"}).status_code == 409
    sid, contra = signal(f), signal(f)
    r = attach(f, cid, sid, "SUPPORTING")
    assert r.status_code == 201, r.text
    assert r.json()["qualification"]["replication_status"] == "REPLICATION_NOT_ESTABLISHED"
    assert patch(f, cid, {"replication_status": "REPLICATION_FAILED"}).status_code == 409
    assert patch(f, cid, {"replication_status": "REPLICATION_EVIDENCE_PRESENT"}).status_code == 200
    assert attach(f, cid, contra).status_code == 409
    r = attach(f, cid, contra, replication_status="REPLICATION_FAILED")
    assert r.status_code == 201
    assert r.json()["qualification"]["consistency_status"] == "MIXED"
    assert dispose(f, cid, contra, "ATTACHMENT_ERROR").status_code == 409
    r = dispose(f, cid, contra, "ATTACHMENT_ERROR", replication_status="REPLICATION_EVIDENCE_PRESENT")
    assert r.status_code == 200
    assert r.json()["qualification"]["consistency_status"] == "SUPPORTING_ONLY"
    assert dispose(f, cid, sid, "ATTACHMENT_ERROR").status_code == 409
    assert dispose(f, cid, sid, "OTHER").status_code == 200  # same effectiveness rule for supporting
    assert attach(f, cid, sid, "SUPPORTING").status_code == 409

@pytest.mark.parametrize("terminal", ["VALIDATED", "REJECTED"])
def test_terminal_subtree_frozen(subject, terminal):
    f, cid = subject; sid = signal(f)
    assert patch(f, cid, SUFFICIENT).status_code == 200
    assert attach(f, cid, sid, "SUPPORTING").status_code == 201
    assert _post(f, _path(f, cid, "decision"), {"decision": terminal}).status_code == 200
    assert patch(f, cid, {"confidence": "HIGH"}).status_code == 409
    assert attach(f, cid, signal(f)).status_code == 409
    assert dispose(f, cid, sid, "ATTACHMENT_ERROR").status_code == 409


def test_insufficient_edit_reopen_and_strict_requests(subject):
    f, cid = subject
    assert _post(f, _path(f, cid, "decision"), {"decision": "INSUFFICIENT_EVIDENCE"}).status_code == 200
    assert patch(f, cid, {"consistency_status": "SUPPORTING_ONLY"}).status_code == 422
    r = patch(f, cid, SUFFICIENT)
    assert r.json()["status"] == "INSUFFICIENT_EVIDENCE"
    sid = signal(f)
    assert attach(f, cid, sid, note=" \t").status_code == 422
    assert attach(f, cid, sid).status_code == 201
    assert dispose(f, cid, sid, "ATTACHMENT_ERROR", removal_note=" ").status_code == 422
    assert dispose(f, cid, sid, "ATTACHMENT_ERROR").status_code == 200
    assert _post(f, _path(f, cid, "mark-validation-pending")).status_code == 200
    assert validate(f, cid).status_code == 200


def test_primary_evidence_cannot_be_reused(subject):
    f, cid = subject
    with Session(get_engine()) as s:
        candidate = s.scalar(select(LearningCandidate).where(LearningCandidate.public_id == cid))
        sid = s.scalar(select(PerformanceSignal.public_id).join(AnalysisResultSignal, AnalysisResultSignal.signal_id == PerformanceSignal.id).where(AnalysisResultSignal.analysis_result_id == candidate.analysis_result_id))
    assert attach(f, cid, sid, "SUPPORTING").status_code == 409


def test_legacy_unqualified_validated_cannot_create_new_implication_or_recommendation(subject):
    """MVP-26/26A-R1 §21/§W: superseded expectation, corrected. Under
    MVP-25 alone, a VALIDATED-but-unqualified row (forced directly here —
    no normal write path produces one, MVP-26A-R1 §U) could still create a
    Recommendation directly, since recommendation creation only checked
    ``status == VALIDATED``. Under MVP-26, every NEW Recommendation
    requires a StrategicImplication, and StrategicImplication creation
    re-checks qualification sufficiency as defense-in-depth — so this
    exact anomaly can no longer produce a NEW Recommendation at all. This
    is the intended effect of closing the new-write bypass (MVP-26A-R1
    §C), not a regression: the qualification PATCH itself remains 409
    because the candidate is still terminal (VALIDATED), exactly as
    before."""
    f, cid = subject
    with Session(get_engine()) as s:
        candidate = s.scalar(select(LearningCandidate).where(LearningCandidate.public_id == cid))
        candidate.status = LearningCandidateStatus.VALIDATED  # historical fixture only
        s.commit()
    body = f["client"].get(f"/api/v1/campaigns/{f['campaign_id']}/learning").json()
    assert body["learning_candidates"][0]["qualification"] is None
    implication = _post(f, _path(f, cid, "strategic-implications"), {"statement": "Legacy eligibility"})
    assert implication.status_code == 409
    assert implication.json()["error"]["code"] == "LEARNING_QUALIFICATION_CONFLICT"
    assert _post(
        f, _path(f, cid, "recommendations"),
        {"strategic_implication_id": "SIM-PLACEHOLDER0", "summary": "Legacy eligibility"},
    ).status_code == 403  # no such implication was ever created
    assert patch(f, cid, SUFFICIENT).status_code == 409


def test_no_cascade_and_effective_uniqueness(subject):
    f, cid = subject; sid = signal(f)
    assert attach(f, cid, sid).status_code == 201
    assert dispose(f, cid, sid, "OTHER").status_code == 200
    with Session(get_engine()) as s:
        candidate = s.scalar(select(LearningCandidate).where(LearningCandidate.public_id == cid))
        q = s.scalar(select(LearningQualification).where(LearningQualification.learning_candidate_id == candidate.id))
        row = s.scalar(select(LearningQualificationSignal).where(LearningQualificationSignal.learning_qualification_id == q.id))
        ids = (q.id, row.performance_signal_id, row.workspace_id)
        with pytest.raises(IntegrityError):
            s.execute(delete(LearningQualification).where(LearningQualification.id == ids[0])); s.commit()
        s.rollback()
        from app.learning.models import EvidenceRelationship
        s.add(LearningQualificationSignal(learning_qualification_id=ids[0], performance_signal_id=ids[1], workspace_id=ids[2], relationship=EvidenceRelationship.SUPPORTING))
        with pytest.raises(IntegrityError) as error:
            s.commit()
        assert error.value.orig.diag.constraint_name == "uq_lqs_effective_pair"
        s.rollback()
        assert s.scalar(select(func.count()).select_from(LearningQualificationSignal).where(LearningQualificationSignal.learning_qualification_id == ids[0])) == 1

@pytest.mark.parametrize("role", ["MEMBER", "ADMIN", "OWNER"])
def test_roles_for_evidence_and_final_decision(subject, role):
    from fastapi.testclient import TestClient
    from app.workspaces.models import MembershipRole
    from tests.settingstest import add_member_to_workspace, login_as
    f, cid = subject
    workspace = f["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace, role=MembershipRole(role))
    with TestClient(f["client"].app, raise_server_exceptions=False) as client:
        token = login_as(client, email=member["email"], password=member["password"])
        actor = {**f, "client": client, "csrf_token": token}
        assert patch(actor, cid, SUFFICIENT).status_code == 200
        sid = signal(f)
        assert attach(actor, cid, sid).status_code == 201
        assert dispose(actor, cid, sid, "ATTACHMENT_ERROR").status_code == 200
        assert validate(actor, cid).status_code == (403 if role == "MEMBER" else 200)

@pytest.mark.parametrize("foreign_workspace", [False, True])
def test_cross_campaign_and_workspace_substitution(subject, foreign_workspace):
    from fastapi.testclient import TestClient
    from tests.campaignstest import campaign_payload, register_and_get_csrf
    f, cid = subject
    other = f.copy()
    if foreign_workspace:
        other["client"] = TestClient(f["client"].app, raise_server_exceptions=False)
        other["csrf_token"] = register_and_get_csrf(other["client"])
    response = other["client"].post("/api/v1/campaigns", json=campaign_payload(name="Foreign campaign"), headers={"X-CSRF-Token": other["csrf_token"]})
    assert response.status_code == 201, response.text
    other["campaign_id"] = response.json()["campaign"]["id"]
    foreign_candidate = _build_candidate(other["campaign_id"], target_status=LearningCandidateStatus.VALIDATION_PENDING, qualified=False)
    sid = signal(other)
    assert attach(f, cid, sid, "SUPPORTING").status_code == 403
    assert patch(f, foreign_candidate, SUFFICIENT).status_code == 403
    assert attach(f, foreign_candidate, sid).status_code == 403
    assert dispose(f, cid, sid, "OTHER").status_code == 403
    if foreign_workspace: other["client"].close()


def test_csrf_delete_and_structural_disposition_constraints(subject):
    f, cid = subject; sid = signal(f)
    assert f["client"].patch(_path(f, cid, "qualification"), json=SUFFICIENT).status_code == 403
    assert attach(f, cid, sid).status_code == 201
    url = _path(f, cid, f"qualification/signals/{sid}")
    assert f["client"].delete(url, headers={"X-CSRF-Token": f["csrf_token"]}).status_code in (404, 405)
    with Session(get_engine()) as s:
        candidate = s.scalar(select(LearningCandidate).where(LearningCandidate.public_id == cid))
        q = s.scalar(select(LearningQualification).where(LearningQualification.learning_candidate_id == candidate.id))
        row = s.scalar(select(LearningQualificationSignal).where(LearningQualificationSignal.learning_qualification_id == q.id))
        from datetime import datetime, timezone
        row.removed_at = datetime.now(timezone.utc)
        with pytest.raises(IntegrityError) as error: s.commit()
        assert "disposition_complete" in error.value.orig.diag.constraint_name
        s.rollback()
        for entity in (candidate, s.get(PerformanceSignal, row.performance_signal_id)):
            s.delete(entity)
            with pytest.raises(IntegrityError): s.commit()
            s.rollback()


def test_multiple_shared_primary_signals_do_not_establish_replication(subject):
    from app.learning.service import LearningService
    from app.measurement.models import AnalysisResult
    f, cid = subject
    sid = signal(f)
    with Session(get_engine()) as s:
        candidate = s.scalar(select(LearningCandidate).where(LearningCandidate.public_id == cid))
        sig = s.scalar(select(PerformanceSignal).where(PerformanceSignal.public_id == sid))
        s.add(AnalysisResultSignal(workspace_id=candidate.workspace_id, analysis_result_id=candidate.analysis_result_id, signal_id=sig.id))
        analysis = s.get(AnalysisResult, candidate.analysis_result_id)
        s.commit()
        second = LearningService(s).record_learning_candidate(analysis_result=analysis, summary="Separate bounded interpretation of same primary signals")
        second_id = second.public_id
    for learning_id in (cid, second_id):
        response = patch(f, learning_id, SUFFICIENT)
        assert response.json()["qualification"]["consistency_status"] == "NOT_ASSESSED"
        assert response.json()["qualification"]["replication_status"] == "REPLICATION_NOT_ESTABLISHED"
        assert attach(f, learning_id, sid, "SUPPORTING").status_code == 409
