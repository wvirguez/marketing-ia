"use client";

// MVP-38: governed Variant Identity — the declared CONDITIONS of one
// Experiment's current comparison definition (frozen MVP-38A/-38B). Minimum
// reachable UI only: list the declared conditions (paginated, "Ver más") and
// declare a new one (label + condition description) behind an explicit
// acknowledgement. There is no role, allocation, traffic, metric, winner,
// result or execution control — none of those exist in MVP-38. Copy says
// "condición" and never claims validity, causality, readiness or a result.
//
// A declared condition pins (fixes) the CURRENT definition version, and the
// system currently offers no way to correct a declared condition
// (MVP38A-OBS-3). The form therefore requires an explicit acknowledgement
// before submission, without claiming the situation is permanent.
//
// Idempotency: `client_request_id` is generated once per section and KEPT
// across retryable failures (a lost response replays as a 200); it rotates
// only on a successful write or IDEMPOTENCY_KEY_CONFLICT. The pin
// (`definition_version_id`) is the id of the tip this section was rendered
// with. On VERSION_NOT_CURRENT / STRATEGY_STALE the draft is discarded and
// the view refetched — a stale draft is never silently rebased.

import { useEffect, useRef, useState } from "react";
import { declareVariant, listVariants } from "@/lib/api/strategy";
import { ApiError } from "@/lib/api/client";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type { ExperimentDefinitionPublic, ExperimentPublic, VariantPublic } from "@/types/strategy";

const LABEL_MAX = 200;
const DESCRIPTION_MAX = 1000;
const PAGE_LIMIT = 100;

export const ACKNOWLEDGEMENT_COPY =
  "Entiendo que declarar esta condición fija la versión actual de la definición y que hoy no puede corregirse.";

const WARNING_COPY =
  "Declarar esta condición fija la versión actual de la definición de la comparación: después no se podrá crear una nueva versión de esa definición. Además, el sistema actualmente no ofrece una forma de corregir una condición ya declarada.";

// MVP-32A §R precedent: the same MEMBER+ tier that may propose an Experiment.
function canDeclareVariant(role: string | null): boolean {
  return role === "OWNER" || role === "ADMIN" || role === "MEMBER";
}

function describeVariantError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "EXPERIMENT_VARIANT_DEFINITION_VERSION_NOT_CURRENT":
        return "La definición cambió mientras declarabas la condición. Se actualizó la vista; revísala y vuelve a intentar.";
      case "EXPERIMENT_VARIANT_STRATEGY_STALE":
        return "Este experimento pertenece a una versión de la estrategia que ya no es la vigente, por lo que no admite nuevas condiciones.";
      case "EXPERIMENT_VARIANT_FROZEN_BY_EXECUTION_START":
        return "No se pueden declarar más condiciones porque se atestiguó el inicio de la ejecución. Revocar la autorización no lo reabre; para cambiarlas hay que crear un nuevo experimento.";
      case "EXPERIMENT_VARIANT_LABEL_DUPLICATE":
        return "Ya existe una condición con esa etiqueta para esta versión de la definición.";
      case "IDEMPOTENCY_KEY_CONFLICT":
        return "Esta solicitud no coincide con un envío anterior. Revisa los datos e inténtalo de nuevo.";
    }
  }
  return describeCampaignError(error);
}

export function ExperimentVariantsSection({
  campaignId,
  experiment,
  definition,
  role,
  onChanged,
}: {
  campaignId: string;
  experiment: ExperimentPublic;
  definition: ExperimentDefinitionPublic;
  role: string | null;
  onChanged: () => void;
}) {
  const [items, setItems] = useState<VariantPublic[]>([]);
  const [total, setTotal] = useState(0);
  const [listError, setListError] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [description, setDescription] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  // Lazy state initializer: the initial key is generated exactly once.
  const [initialClientRequestId] = useState(() => crypto.randomUUID());
  const clientRequestIdRef = useRef(initialClientRequestId);
  const idPrefix = `variant-${experiment.id}`;

  // Initial page: only when the definition reports declared conditions.
  // Refetches when a Variant is created, the definition id changes, or the
  // derived count changes. The Strategy payload never embeds the list.
  useEffect(() => {
    // Nothing to fetch (and nothing rendered from `items`) without declared conditions.
    if (definition.variant_count === 0) return;
    let cancelled = false;
    listVariants(campaignId, experiment.id, { limit: PAGE_LIMIT, offset: 0 })
      .then((page) => {
        if (cancelled) return;
        setItems(page.items);
        setTotal(page.total);
        setListError("");
      })
      .catch((caught) => {
        if (!cancelled) setListError(describeCampaignError(caught));
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId, experiment.id, definition.id, definition.variant_count, reloadToken]);

  async function loadMore() {
    if (loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await listVariants(campaignId, experiment.id, { limit: PAGE_LIMIT, offset: items.length });
      setItems((current) => [...current, ...page.items]);
      setTotal(page.total);
      setListError("");
    } catch (caught) {
      setListError(describeCampaignError(caught));
    } finally {
      setLoadingMore(false);
    }
  }

  function openForm() {
    setLabel("");
    setDescription("");
    setAcknowledged(false);
    setError("");
    setOpen(true);
  }

  function closeForm() {
    setOpen(false);
    setLabel("");
    setDescription("");
    setAcknowledged(false);
  }

  function validate(): string | null {
    const trimmedLabel = label.trim();
    const trimmedDescription = description.trim();
    if (trimmedLabel.length === 0) return 'El campo "Etiqueta de la condición" es obligatorio.';
    if (trimmedLabel.length > LABEL_MAX) return `La etiqueta no puede superar ${LABEL_MAX} caracteres.`;
    if (trimmedDescription.length === 0) return 'El campo "Descripción de la condición" es obligatorio.';
    if (trimmedDescription.length > DESCRIPTION_MAX) {
      return `La descripción no puede superar ${DESCRIPTION_MAX} caracteres.`;
    }
    return null;
  }

  async function submit() {
    if (pending || !acknowledged) return;
    const invalid = validate();
    if (invalid) {
      setError(invalid);
      return;
    }
    setPending(true);
    setError("");
    try {
      await declareVariant(campaignId, experiment.id, {
        definition_version_id: definition.id,
        label: label.trim(),
        condition_description: description.trim(),
        client_request_id: clientRequestIdRef.current,
      });
      clientRequestIdRef.current = crypto.randomUUID();
      closeForm();
      setReloadToken((token) => token + 1);
      onChanged();
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : null;
      if (code === "IDEMPOTENCY_KEY_CONFLICT") {
        clientRequestIdRef.current = crypto.randomUUID();
      }
      setError(describeVariantError(caught));
      if (
        code === "EXPERIMENT_VARIANT_DEFINITION_VERSION_NOT_CURRENT" ||
        code === "EXPERIMENT_VARIANT_STRATEGY_STALE"
      ) {
        // Never silently rebase a stale draft: discard it and refetch.
        closeForm();
        onChanged();
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <div style={{ marginTop: 12 }}>
      <p className="muted small-text">
        <strong>Condiciones declaradas</strong>
        {definition.variant_count > 0 ? ` · ${definition.variant_count}` : ""}
      </p>

      {definition.variant_count === 0 ? (
        <p className="muted small-text">Aún no se han declarado condiciones para esta versión de la definición.</p>
      ) : (
        <>
          <ol className="small-text" style={{ marginTop: 4 }}>
            {items.map((variant) => (
              <li key={variant.id}>
                <strong>{variant.label}</strong>
                <span style={{ whiteSpace: "pre-wrap", display: "block" }}>{variant.condition_description}</span>
              </li>
            ))}
          </ol>
          {items.length < total && (
            <div className="settings-form-actions" style={{ marginTop: 4 }}>
              <button type="button" className="button" disabled={loadingMore} onClick={loadMore}>
                Ver más
              </button>
              <span className="muted small-text">
                {" "}
                Mostrando {items.length} de {total}
              </span>
            </div>
          )}
          {listError && (
            <p role="alert" className="settings-feedback">
              {listError}
            </p>
          )}
        </>
      )}

      {definition.is_pinned && (
        <p className="muted small-text" style={{ marginTop: 4 }}>
          Esta versión de la definición está fijada por las condiciones declaradas.
        </p>
      )}

      {error && !open && (
        <p role="alert" className="settings-feedback">
          {error}
        </p>
      )}

      {canDeclareVariant(role) && !open && (
        <div className="settings-form-actions" style={{ marginTop: 8 }}>
          <button type="button" className="button" onClick={openForm}>
            Agregar condición
          </button>
        </div>
      )}

      {open && (
        <div className="panel" style={{ marginTop: 8 }}>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-label`}>Etiqueta de la condición</label>
            <input
              id={`${idPrefix}-label`}
              type="text"
              value={label}
              disabled={pending}
              onChange={(event) => setLabel(event.target.value)}
            />
          </div>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-description`}>Descripción de la condición</label>
            <textarea
              id={`${idPrefix}-description`}
              value={description}
              disabled={pending}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          <p className="muted small-text">{WARNING_COPY}</p>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-ack`}>
              <input
                id={`${idPrefix}-ack`}
                type="checkbox"
                checked={acknowledged}
                disabled={pending}
                onChange={(event) => setAcknowledged(event.target.checked)}
              />{" "}
              {ACKNOWLEDGEMENT_COPY}
            </label>
          </div>
          <div className="settings-form-actions" style={{ marginTop: 8 }}>
            <button type="button" className="button primary" disabled={pending || !acknowledged} onClick={submit}>
              Confirmar condición
            </button>
            <button type="button" className="button" disabled={pending} onClick={closeForm}>
              Cancelar
            </button>
          </div>
          {error && (
            <p role="alert" className="settings-feedback">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
