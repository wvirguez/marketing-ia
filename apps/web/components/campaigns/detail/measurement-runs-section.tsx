"use client";

// Experiment Measurement (frozen Discovery / Design Freeze / both Reconciliations). Minimum operational UI
// in the Start section: a "run Measurement" action for THIS started execution attempt and the immutable,
// historical list of every Run it has ever produced.
//
// MEASUREMENT != RESULT != WINNER != HYPOTHESIS VERDICT != EXPERIMENTAL VALIDITY != CAUSALITY != LEARNING.
// A Run records ONLY temporal classification, structural qualification, coverage facts, pairing facts,
// descriptive values and strictly bounded comparative arithmetic, plus disclosures — never a success/failure
// state, a winner, a validity verdict, or a causal/learning claim. `signed_arithmetic_difference` means ONLY
// arithmetic subtraction (observation - baseline); it is never rendered as lift, effect, improvement, rate
// or significance. Runs are APPEND-ONLY: a later Run never invalidates an earlier one, and there is no
// update/delete control here.
//
// Idempotency: `client_request_id` is generated fresh for every "run Measurement" click (an intentional new
// invocation, per the frozen protocol) — never reused across clicks, since reusing it would replay the
// SAME historical Run rather than compute a new one.

import { useEffect, useState } from "react";
import { createExperimentMeasurementRun, listExperimentMeasurementRuns } from "@/lib/api/strategy";
import { ApiError } from "@/lib/api/client";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type {
  ExperimentMeasurementDatumUsagePublic,
  ExperimentMeasurementRunPublic,
  ExperimentMeasurementSignalOutputPublic,
} from "@/types/strategy";

// The same MEMBER+ tier as every other write in this chain (frozen §Y — AGENT-07's conceptual ownership
// never implies a stronger human workspace role).
function canRunMeasurement(role: string | null): boolean {
  return role === "OWNER" || role === "ADMIN" || role === "MEMBER";
}

function describeRunError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "EXPERIMENT_MEASUREMENT_IDEMPOTENCY_KEY_CONFLICT":
        return "Esta solicitud no coincide con un envío anterior de esta misma medición. Vuelve a intentarlo.";
      case "EXPERIMENT_MEASUREMENT_RETRY_REQUIRED":
        return "No fue posible calcular la medición bajo una vista consistente. Inténtalo de nuevo.";
    }
  }
  return describeCampaignError(error);
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString("es");
}

const USAGE_DECISION_LABEL: Record<ExperimentMeasurementDatumUsagePublic["usage_decision"], string> = {
  CONSUMED: "incluido",
  EXCLUDED_AMBIGUOUS: "excluido · límite ambiguo",
  EXCLUDED_OUT_OF_WINDOW: "excluido · fuera de la ventana",
  EXCLUDED_MULTI_SIGNAL: "excluido · reclamado bajo más de una señal",
  EXCLUDED_CONFLICT: "excluido · se superpone con otro dato del mismo rol",
};

const TEMPORAL_ROLE_LABEL: Record<string, string> = {
  BASELINE: "base",
  OBSERVATION: "observación",
};

function SignalOutputView({ signal }: { signal: ExperimentMeasurementSignalOutputPublic }) {
  return (
    <li style={{ marginBottom: 8 }}>
      <strong>{signal.required_signal_id}</strong> ·{" "}
      {signal.declaration_level === "DESCRIPTIVE" ? "declaración descriptiva" : "declaración comparativa"}
      {signal.slices.length === 0 ? (
        <p className="muted small-text" style={{ margin: "2px 0 0" }}>
          Sin datos afirmados para esta señal en este inicio (ningún canal reclamado).
        </p>
      ) : (
        <ul style={{ margin: "2px 0 0", paddingLeft: 18 }}>
          {signal.slices.map((slice) => (
            <li key={slice.channel} className="small-text">
              canal <strong>{slice.channel}</strong> ·{" "}
              {slice.coverage_state !== null && (
                <>
                  {slice.qualifying_count}/{slice.required_count ?? "—"} datos calificados ·{" "}
                  {slice.coverage_state === "COVERED" ? "cobertura alcanzada" : "cobertura no alcanzada"}
                </>
              )}
              {slice.pairing_state !== null && (
                <>
                  emparejamiento:{" "}
                  {slice.pairing_state === "PAIR"
                    ? "par establecido"
                    : slice.pairing_state === "INCOMPLETE"
                      ? "incompleto"
                      : slice.pairing_state === "SURPLUS"
                        ? "excedente (más de un dato por lado)"
                        : "duración de periodo distinta entre base y observación"}
                  {slice.pairing_state === "PAIR" && (
                    <>
                      {" "}
                      · base {slice.baseline_value} · observación {slice.observation_value} · diferencia
                      aritmética {slice.signed_arithmetic_difference} (resta simple; no es una tasa, un
                      porcentaje, un efecto ni una medida de significancia)
                    </>
                  )}
                </>
              )}
              {" · "}excluidos: {slice.ambiguous_excluded_count} ambiguos, {slice.conflict_excluded_count} en
              conflicto, {slice.multi_signal_excluded_count} multi-señal, {slice.out_of_window_count} fuera de
              ventana
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function DatumUsageView({ usage }: { usage: ExperimentMeasurementDatumUsagePublic }) {
  return (
    <li className="small-text">
      <strong>{usage.claim_id}</strong> · señal {usage.required_signal_id} · canal {usage.channel} ·{" "}
      {USAGE_DECISION_LABEL[usage.usage_decision]}
      {usage.temporal_role && <> · rol: {TEMPORAL_ROLE_LABEL[usage.temporal_role] ?? usage.temporal_role}</>}
      {usage.recorded_before_declaration && <> · registrado antes de esta declaración</>}
      {usage.later_grouping_entry_exists_at_run && (
        <> · existía una entrada posterior en la misma agrupación al momento de esta medición (solo aviso)</>
      )}
    </li>
  );
}

function RunView({ run }: { run: ExperimentMeasurementRunPublic }) {
  return (
    <li style={{ marginBottom: 12 }}>
      <strong>{run.id}</strong> · {formatDate(run.created_at)} ·{" "}
      {run.legacy ? "medición heredada (POST_HOC)" : `semántica versión ${run.declaration_semantics_version}`}
      {run.legacy && (
        <p className="muted small-text" style={{ margin: "2px 0 0" }}>
          Este contrato de medición no tiene una declaración estructurada previa a la ejecución: esta medición
          es solo un listado de procedencia posterior a los hechos (POST_HOC), sin calificación temporal, sin
          cobertura ni emparejamiento.
        </p>
      )}
      {run.signal_outputs.length > 0 && (
        <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
          {run.signal_outputs.map((signal) => (
            <SignalOutputView key={signal.required_signal_id} signal={signal} />
          ))}
        </ul>
      )}
      <details style={{ marginTop: 4 }}>
        <summary className="small-text muted">
          Procedencia de cada dato considerado ({run.datum_usages.length})
        </summary>
        <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
          {run.datum_usages.map((usage, index) => (
            <DatumUsageView key={`${usage.claim_id}-${index}`} usage={usage} />
          ))}
        </ul>
      </details>
    </li>
  );
}

export function MeasurementRunsSection({
  campaignId,
  experimentId,
  startId,
  role,
}: {
  campaignId: string;
  experimentId: string;
  startId: string;
  role: string | null;
}) {
  const [runs, setRuns] = useState<ExperimentMeasurementRunPublic[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [listError, setListError] = useState("");
  const [reloadToken, setReloadToken] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const idPrefix = `measurement-runs-${startId}`;

  useEffect(() => {
    let cancelled = false;
    listExperimentMeasurementRuns(campaignId, experimentId, startId)
      .then((response) => {
        if (!cancelled) {
          setRuns(response.runs);
          setListError("");
          setLoaded(true);
        }
      })
      .catch((caught) => {
        if (!cancelled) {
          setListError(describeCampaignError(caught));
          setLoaded(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId, experimentId, startId, reloadToken]);

  async function submitRun() {
    if (pending) return;
    setPending(true);
    setError("");
    try {
      // A fresh key every click: each click is a deliberate new invocation
      // (frozen §H) — never a retry of a prior one.
      await createExperimentMeasurementRun(campaignId, experimentId, startId, {
        client_request_id: crypto.randomUUID(),
      });
      setReloadToken((token) => token + 1);
    } catch (caught) {
      setError(describeRunError(caught));
    } finally {
      setPending(false);
    }
  }

  return (
    <div style={{ marginTop: 16 }} data-testid="measurement-runs-section">
      <p className="muted small-text">
        <strong>Mediciones del experimento</strong> · nivel observacional · inicio {startId}
      </p>
      <p className="muted small-text" role="note" style={{ marginTop: 4 }}>
        <strong>SOLO MEDICIÓN OBSERVACIONAL — NO ES UN RESULTADO, UN VEREDICTO NI UN APRENDIZAJE VALIDADO.</strong>{" "}
        Cada medición es un cálculo histórico e inmutable de la evidencia afirmada bajo este inicio: qué se
        incluyó, qué se excluyó y por qué, cobertura y emparejamiento cuando corresponde, y valores
        descriptivos o comparativos. No establece elegibilidad, validez, éxito, causalidad, atribución
        comercial ni un ganador. Una medición posterior nunca invalida una anterior.
      </p>

      {listError && (
        <p role="alert" className="settings-feedback">
          {listError}
        </p>
      )}

      {loaded && !listError && runs.length === 0 && (
        <p className="muted small-text" style={{ marginTop: 4 }}>
          Aún no se ha calculado ninguna medición para este inicio.
        </p>
      )}

      {runs.length > 0 && (
        <ol className="small-text" style={{ margin: "4px 0 0", paddingLeft: 18 }} data-testid={`${idPrefix}-list`}>
          {runs.map((run) => (
            <RunView key={run.id} run={run} />
          ))}
        </ol>
      )}

      {error && (
        <p role="alert" className="settings-feedback">
          {error}
        </p>
      )}

      {canRunMeasurement(role) && (
        <div className="settings-form-actions" style={{ marginTop: 8 }}>
          <button type="button" className="button" disabled={pending} onClick={submitRun}>
            Calcular medición
          </button>
        </div>
      )}
    </div>
  );
}
