"""MVP-25 governance. Human claims only; no statistical or causal inference.

All mutations serialize on the candidate. OTHER remains governance-effective
for SUPPORTING as well as CONTRADICTING (R2 §P/W); only ATTACHMENT_ERROR is
excluded. This is the single effectiveness definition for reads and writes.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.core.api_errors import ApiError, ForbiddenError
from app.learning.models import (
    LearningCandidateStatus, LearningQualification, LearningQualificationSignal,
    EvidenceRelationship, EvidenceRemovalReason, ReplicationStatus,
)
from app.learning.repository import LearningCandidateRepository
from app.measurement.models import AnalysisResultSignal, PerformanceSignal
from app.users.models import User


class QualificationConflict(ApiError):
    def __init__(self, message="Learning qualification is not sufficient or coherent."):
        super().__init__(message, status_code=409, code="LEARNING_QUALIFICATION_CONFLICT")


def effective(row):
    return row.removal_reason != EvidenceRemovalReason.ATTACHMENT_ERROR


def consistency(rows):
    supporting = any(effective(r) and r.relationship == EvidenceRelationship.SUPPORTING for r in rows)
    contradicting = any(effective(r) and r.relationship == EvidenceRelationship.CONTRADICTING for r in rows)
    return "MIXED" if supporting and contradicting else "SUPPORTING_ONLY" if supporting else "CONTRADICTING" if contradicting else "NOT_ASSESSED"


def coherence_errors(replication, rows):
    state = consistency(rows)
    if replication == ReplicationStatus.REPLICATION_EVIDENCE_PRESENT and state != "SUPPORTING_ONLY":
        return ["REPLICATION_REQUIRES_SUPPORTING_ONLY"]
    if replication == ReplicationStatus.REPLICATION_FAILED and not any(effective(r) and r.relationship == EvidenceRelationship.CONTRADICTING and r.note and r.note.strip() for r in rows):
        return ["REPLICATION_FAILED_REQUIRES_CONTRADICTION"]
    return []


def sufficiency_errors(qualification, rows):
    if qualification is None:
        return ["QUALIFICATION_REQUIRED"]
    errors = coherence_errors(qualification.replication_status, rows)
    if qualification.confidence is None:
        errors.append("CONFIDENCE_REQUIRED")
    if not qualification.scope or not qualification.scope.strip():
        errors.append("SCOPE_REQUIRED")
    if not qualification.generalization_boundary or not qualification.generalization_boundary.strip():
        errors.append("GENERALIZATION_BOUNDARY_REQUIRED")
    if consistency(rows) in ("MIXED", "CONTRADICTING"):
        errors.append("CONTRADICTING_EVIDENCE")
    if qualification.replication_status == ReplicationStatus.REPLICATION_FAILED:
        errors.append("REPLICATION_FAILED")
    return errors


class QualificationService:
    def __init__(self, session):
        self.session = session

    def read(self, candidate):
        q = self.session.execute(select(LearningQualification).where(
            LearningQualification.learning_candidate_id == candidate.id,
            LearningQualification.workspace_id == candidate.workspace_id,
        ).execution_options(populate_existing=True)).scalar_one_or_none()
        rows = [] if q is None else list(self.session.scalars(select(LearningQualificationSignal).where(
            LearningQualificationSignal.learning_qualification_id == q.id,
            LearningQualificationSignal.workspace_id == candidate.workspace_id,
        ).order_by(LearningQualificationSignal.created_at, LearningQualificationSignal.id).execution_options(populate_existing=True)))
        return q, rows

    def lock(self, campaign, candidate_public_id):
        candidate = LearningCandidateRepository(self.session).get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=candidate_public_id, for_update=True)
        if candidate is None or candidate.workspace_id != campaign.workspace_id:
            raise ForbiddenError()
        if candidate.status in (LearningCandidateStatus.VALIDATED, LearningCandidateStatus.REJECTED):
            raise QualificationConflict("Terminal Learning qualification is frozen.")
        return candidate

    def require_sufficient(self, candidate):
        q, rows = self.read(candidate)
        errors = sufficiency_errors(q, rows)
        if errors:
            raise QualificationConflict(", ".join(errors))

    def mutate(self, *, campaign, candidate_public_id, actor_user_id, operation, values, signal_public_id=None, request_id=None):
        if operation not in ("update", "attach", "dispose"):
            raise ValueError("Unknown qualification operation")
        candidate = self.lock(campaign, candidate_public_id)
        q, rows = self.read(candidate)
        try:
            if q is None:
                if operation == "dispose":
                    raise ForbiddenError()
                q = LearningQualification(workspace_id=candidate.workspace_id, learning_candidate_id=candidate.id)
                self.session.add(q)
                self.session.flush()
            signal_id = None
            previous = None
            if operation == "update":
                for field, value in values.items():
                    setattr(q, field, value)
                event = "learning.qualification.updated"
                new = "QUALIFICATION_UPDATED"
            elif operation == "attach":
                signal = self.session.scalar(select(PerformanceSignal).where(
                    PerformanceSignal.public_id == values["performance_signal_id"],
                    PerformanceSignal.campaign_id == campaign.id,
                    PerformanceSignal.workspace_id == campaign.workspace_id))
                if signal is None:
                    raise ForbiddenError()
                primary = self.session.scalar(select(AnalysisResultSignal.id).where(
                    AnalysisResultSignal.analysis_result_id == candidate.analysis_result_id,
                    AnalysisResultSignal.signal_id == signal.id))
                if primary is not None:
                    raise QualificationConflict("Primary evidence cannot be additional qualification evidence.")
                if any(r.performance_signal_id == signal.id and effective(r) for r in rows):
                    raise QualificationConflict("This signal already has governance-effective evidence.")
                row = LearningQualificationSignal(workspace_id=candidate.workspace_id, learning_qualification_id=q.id,
                    performance_signal_id=signal.id, relationship=values["relationship"], note=values.get("note"))
                if row.relationship == EvidenceRelationship.CONTRADICTING and (not row.note or not row.note.strip()):
                    raise QualificationConflict("Contradicting evidence requires a non-empty note.")
                self.session.add(row)
                rows.append(row)
                signal_id = signal.id
                event, new = "learning.qualification.signal_attached", row.relationship.value
            else:
                signal = self.session.scalar(select(PerformanceSignal).where(
                    PerformanceSignal.public_id == signal_public_id, PerformanceSignal.campaign_id == campaign.id,
                    PerformanceSignal.workspace_id == campaign.workspace_id))
                if signal is None:
                    raise ForbiddenError()
                matches = [r for r in rows if r.performance_signal_id == signal.id and r.removed_at is None]
                if not matches:
                    raise QualificationConflict("No current attachment exists for this signal.")
                row = matches[0]
                if not values["removal_note"].strip():
                    raise QualificationConflict("Disposition requires a non-empty note.")
                row.removed_at = datetime.now(timezone.utc)
                row.removal_reason = values["removal_reason"]
                row.removal_note = values["removal_note"]
                row.removed_by_user_id = actor_user_id
                signal_id, previous = signal.id, row.relationship.value
                event = "learning.qualification.signal_dispositioned"
                new = f"{previous}_REMOVED_{row.removal_reason.value}"
            if operation != "update" and "replication_status" in values:
                q.replication_status = values["replication_status"]
            errors = coherence_errors(q.replication_status, rows)
            if errors:
                raise QualificationConflict("Explicit coherent replication_status required: " + ", ".join(errors))
            q.updated_at = datetime.now(timezone.utc)
            self.session.flush()
            AuditEventRepository(self.session).record(
                workspace_id=candidate.workspace_id, event_type=event, actor_type=ActorType.USER,
                actor_user_id=actor_user_id, learning_candidate_id=candidate.id, performance_signal_id=signal_id,
                previous_state=previous, new_state=new, request_id=request_id)
            self.session.commit()
            return candidate
        except IntegrityError as exc:
            self.session.rollback()
            name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if name in ("uq_lqs_active_pair", "uq_lqs_effective_pair", "uq_lq_candidate"):
                raise QualificationConflict("Duplicate qualification evidence.") from exc
            raise
        except Exception:
            self.session.rollback()
            raise

    def public(self, candidate):
        from app.learning.qualification_schemas import QualificationPublic, QualificationEvidencePublic
        q, rows = self.read(candidate)
        if q is None:
            return None
        signals = {s.id: s.public_id for s in self.session.scalars(select(PerformanceSignal).where(
            PerformanceSignal.id.in_([r.performance_signal_id for r in rows])))}
        users = {u.id: u.public_id for u in self.session.scalars(select(User).where(
            User.id.in_([r.removed_by_user_id for r in rows if r.removed_by_user_id])))}
        return QualificationPublic(
            confidence=q.confidence, replication_status=q.replication_status, consistency_status=consistency(rows),
            scope=q.scope, generalization_boundary=q.generalization_boundary, created_at=q.created_at, updated_at=q.updated_at,
            supporting_signal_ids=[signals[r.performance_signal_id] for r in rows if effective(r) and r.relationship == EvidenceRelationship.SUPPORTING],
            contradicting_signal_ids=[signals[r.performance_signal_id] for r in rows if effective(r) and r.relationship == EvidenceRelationship.CONTRADICTING],
            validation_blockers=sufficiency_errors(q, rows),
            evidence=[QualificationEvidencePublic(
                performance_signal_id=signals[r.performance_signal_id], relationship=r.relationship, note=r.note,
                created_at=r.created_at, removed_at=r.removed_at, removal_reason=r.removal_reason,
                removal_note=r.removal_note, removed_by_user_id=users.get(r.removed_by_user_id),
                governance_effective=effective(r), blocks_validation=effective(r) and r.relationship == EvidenceRelationship.CONTRADICTING,
            ) for r in rows],
        )
