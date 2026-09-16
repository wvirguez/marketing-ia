"use client";

import { useState } from "react";
import type { LearningCandidatePublic, QualificationPatch, QualificationAttach, QualificationDispose, ReplicationStatus, QualificationConfidence, EvidenceRelationship, EvidenceRemovalReason } from "@/types/learning";

export type QualificationMutation =
  | { kind: "update"; body: QualificationPatch }
  | { kind: "attach"; body: QualificationAttach }
  | { kind: "dispose"; signalId: string; body: QualificationDispose };

const replicationLabels: Record<ReplicationStatus, string> = {
  REPLICATION_NOT_ESTABLISHED: "Replicación no establecida",
  REPLICATION_EVIDENCE_PRESENT: "Evidencia de replicación declarada",
  REPLICATION_FAILED: "Intento de replicación fallido declarado",
};
const consistencyLabels = { NOT_ASSESSED: "Sin evaluar", SUPPORTING_ONLY: "Solo respaldo", MIXED: "Mixta", CONTRADICTING: "Contradictoria" };
const confidenceLabels = { LOW: "Baja", MEDIUM: "Media", HIGH: "Alta" };
const blockers: Record<string, string> = {
  QUALIFICATION_REQUIRED: "Registra la calificación antes de validar.",
  CONFIDENCE_REQUIRED: "Falta indicar la confianza.",
  SCOPE_REQUIRED: "Falta indicar el alcance.",
  GENERALIZATION_BOUNDARY_REQUIRED: "Falta indicar el límite de generalización.",
  CONTRADICTING_EVIDENCE: "Este aprendizaje tiene evidencia contradictoria que todavía cuenta en su base de calificación. No puede validarse mientras esa contradicción siga vigente.",
  REPLICATION_FAILED: "Un intento de replicación fallido impide validar.",
  REPLICATION_REQUIRES_SUPPORTING_ONLY: "La declaración de replicación requiere evidencia adicional de respaldo sin contradicciones.",
  REPLICATION_FAILED_REQUIRES_CONTRADICTION: "El intento fallido requiere evidencia contradictoria con una justificación.",
};

export function LearningQualification({ candidate, editable, pending, onMutate }: {
  candidate: LearningCandidatePublic;
  editable: boolean;
  pending: boolean;
  onMutate: (candidateId: string, mutation: QualificationMutation) => void;
}) {
  const q = candidate.qualification;
  const [confidence, setConfidence] = useState<QualificationConfidence | "">(q?.confidence ?? "");
  const [replication, setReplication] = useState<ReplicationStatus | "">(q?.replication_status ?? "");
  const [scope, setScope] = useState(q?.scope ?? "");
  const [boundary, setBoundary] = useState(q?.generalization_boundary ?? "");
  const [signal, setSignal] = useState("");
  const [relationship, setRelationship] = useState<EvidenceRelationship>("SUPPORTING");
  const [note, setNote] = useState("");
  const [reason, setReason] = useState<EvidenceRemovalReason>("ATTACHMENT_ERROR");
  const [removalNote, setRemovalNote] = useState("");
  // Explicit atomic claim change, independently chosen from the scalar edit form.
  const [atomicReplication, setAtomicReplication] = useState<ReplicationStatus | "">("");
  const claim = atomicReplication ? { replication_status: atomicReplication } : {};
  const frozen = candidate.status === "VALIDATED" || candidate.status === "REJECTED";
  const mayEdit = editable && !frozen;
  return <section aria-label={`Calificación de ${candidate.id}`} style={{ marginTop: 16 }}>
    <h3>Calificación del aprendizaje</h3>
    <p className="muted small-text">Validado significa aceptado con un alcance acotado según la evidencia actual. No demuestra causalidad, significancia estadística ni una verdad universal.</p>
    {!q && <p>{candidate.status === "VALIDATED" ? "Calificación no registrada — este aprendizaje se validó antes de que existiera este proceso de calificación." : "Calificación no registrada."}</p>}
    {q && <>
      <p>Confianza: {q.confidence ? confidenceLabels[q.confidence] : "Sin registrar"}</p>
      <p>Replicación: {q.replication_status ? replicationLabels[q.replication_status] : "Sin registrar"} — Reclamo humano, no verificado por el sistema.</p>
      <p>Consistencia: {consistencyLabels[q.consistency_status]}</p>
      <p>Alcance: {q.scope || "Sin registrar"}</p>
      <p>Límite de generalización: {q.generalization_boundary || "Sin registrar"}</p>
      <p className="muted small-text">Evidencia adicional que respalda este aprendizaje, según la persona que la vinculó. Una señal adicional no establece replicación automáticamente.</p>
      <ul>{q.evidence.map((row) => <li key={`${row.performance_signal_id}:${row.created_at}`}>
        <strong>{row.performance_signal_id}</strong> — {row.relationship === "SUPPORTING" ? "Respalda" : "Contradice"}
        <p>{row.note}</p>
        <p>{row.removal_reason === "ATTACHMENT_ERROR" ? "Historial: error de vinculación; excluida de la calificación." : row.removal_reason === "OTHER" ? "Disposición OTHER: sigue contando para la gobernanza; la disposición no borra su efecto." : "Evidencia actual"}</p>
        {row.blocks_validation && <p role="note">Esta señal bloquea la validación.</p>}
        {row.removal_reason && <p>Motivo: {row.removal_note}. Registrado por {row.removed_by_user_id} el {row.removed_at}.</p>}
        {mayEdit && !row.removed_at && <button type="button" className="button" disabled={pending || !removalNote.trim()} onClick={() => onMutate(candidate.id, { kind: "dispose", signalId: row.performance_signal_id, body: { removal_reason: reason, removal_note: removalNote, ...claim } })}>Registrar disposición de {row.performance_signal_id}</button>}
      </li>)}</ul>
    </>}
    <p className="muted small-text">Confianza: valoración humana categórica; no representa probabilidad ni confianza estadística.</p>
    <p className="muted small-text">Replicación: Valoración humana; no verificada automáticamente por el sistema.</p>
    {!frozen && <ul aria-label="Requisitos pendientes">{(q?.validation_blockers ?? ["QUALIFICATION_REQUIRED"]).map(code => <li key={code}>{blockers[code] ?? code}</li>)}</ul>}
    {frozen && <p>Calificación e historial congelados por la decisión final.</p>}
    {mayEdit && <fieldset disabled={pending}>
      <legend>Registrar valoración humana</legend>
      <label>Confianza<select value={confidence} onChange={e => setConfidence(e.target.value as QualificationConfidence | "")}><option value="">Sin registrar</option>{Object.entries(confidenceLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>Declaración de replicación<select value={replication} onChange={e => setReplication(e.target.value as ReplicationStatus | "")}><option value="">Sin registrar</option>{Object.entries(replicationLabels).map(([value, label]) => <option key={value} value={value}>{label} — reclamo humano no verificado</option>)}</select></label>
      <label>Alcance<textarea maxLength={4000} value={scope} onChange={e => setScope(e.target.value)} /></label>
      <label>Límite de generalización<textarea maxLength={4000} value={boundary} onChange={e => setBoundary(e.target.value)} /></label>
      <button type="button" className="button" onClick={() => onMutate(candidate.id, { kind: "update", body: { confidence: confidence || null, replication_status: replication || null, scope, generalization_boundary: boundary } })}>Guardar calificación</button>
      <h4>Evidencia adicional</h4>
      <label>ID de señal adicional<input maxLength={20} value={signal} onChange={e => setSignal(e.target.value)} /></label>
      <label>Relación<select value={relationship} onChange={e => setRelationship(e.target.value as EvidenceRelationship)}><option value="SUPPORTING">Respalda</option><option value="CONTRADICTING">Contradice</option></select></label>
      <label>Justificación de la evidencia<textarea maxLength={4000} value={note} onChange={e => setNote(e.target.value)} /></label>
      <label>Declaración para esta vinculación o disposición<select value={atomicReplication} onChange={e => setAtomicReplication(e.target.value as ReplicationStatus | "")}><option value="">Conservar declaración existente</option>{Object.entries(replicationLabels).map(([value, label]) => <option key={value} value={value}>{label} — reclamo humano no verificado</option>)}</select></label>
      <button type="button" className="button" disabled={!signal.trim() || (relationship === "CONTRADICTING" && !note.trim())} onClick={() => onMutate(candidate.id, { kind: "attach", body: { performance_signal_id: signal.trim(), relationship, note, ...claim } })}>Vincular evidencia adicional</button>
      <p>Si esta señal se vinculó por error, puedes marcarla como error de vinculación e indicar el motivo. La señal permanecerá registrada en el historial.</p>
      <label>Motivo de disposición<select value={reason} onChange={e => setReason(e.target.value as EvidenceRemovalReason)}><option value="ATTACHMENT_ERROR">Error de vinculación</option><option value="OTHER">Otro motivo — conserva efecto de gobernanza</option></select></label>
      <label>Justificación de la disposición<textarea maxLength={4000} value={removalNote} onChange={e => setRemovalNote(e.target.value)} /></label>
    </fieldset>}
  </section>;
}
