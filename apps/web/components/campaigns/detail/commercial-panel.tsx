"use client";

// MVP-27B: real Commercial panel for the campaign "Definición comercial"
// section — "what the Campaign is commercially trying to achieve"
// (Commercial Objective) and "what is commercially being offered" (Offer).
// Every mutation goes through the real, authenticated, CSRF-protected
// backend routes (apps/api/app/commercial/router.py) — no optimistic/fake
// state anywhere in this file.
//
// Hard invariants preserved throughout (apps/api/app/commercial/models.py,
// apps/api/app/commercial/service.py, MVP-27A-R1/-R2): 0..N simultaneously
// current rows per Campaign for both entities — no PRIMARY, no version, no
// target field. "Agregar" (independent create) and "Reemplazar" (atomic
// supersede) are two distinct, never-conflated actions — replacement is
// never implemented by editing an existing row in place; it always creates
// a fresh replacement row and marks the original historical.
//
// Single-flight: at most one Commercial mutation may be in flight at a
// time from this panel, mirroring TrackingPanel's own `pending` guard.

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Icon } from "@/components/ui/icon";
import {
  correctCommercialOutcome,
  createCommercialObjective,
  createCommercialOutcome,
  createOffer,
  getCommercialObjectives,
  getCommercialOutcomes,
  getOffers,
  supersedeCommercialObjective,
  supersedeOffer,
} from "@/lib/api/commercial";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type { CommercialObjectivePublic, CommercialOutcomePublic, OfferPublic } from "@/types/commercial";

type Result =
  | { token: number; status: "error"; message: string }
  | {
      token: number;
      status: "ready";
      objectives: CommercialObjectivePublic[];
      offers: OfferPublic[];
      outcomes: CommercialOutcomePublic[];
    };

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("es-ES", { year: "numeric", month: "short", day: "numeric" });
}

function formatPrice(price: string | null, currency: string | null): string {
  if (price === null || currency === null) return "Precio no definido";
  return Number(price) === 0 ? `Gratis (${currency})` : `${price} ${currency}`;
}

function ReplaceForm({
  pending,
  placeholder,
  onSubmit,
  onCancel,
  offerFields,
}: {
  pending: boolean;
  placeholder: string;
  onSubmit: (statement: string, price: string, currency: string) => void;
  onCancel: () => void;
  offerFields?: boolean;
}) {
  const [statement, setStatement] = useState("");
  const [price, setPrice] = useState("");
  const [currency, setCurrency] = useState("");
  const pairInvalid = offerFields ? price.trim().length > 0 !== currency.trim().length > 0 : false;
  const canSubmit = statement.trim().length > 0 && !pairInvalid && !pending;

  return (
    <div className="panel" style={{ marginTop: 8 }}>
      <div className="settings-field">
        <label htmlFor={`${placeholder}-statement`}>Nuevo enunciado</label>
        <textarea
          id={`${placeholder}-statement`}
          value={statement}
          disabled={pending}
          onChange={(event) => setStatement(event.target.value)}
        />
      </div>
      {offerFields && (
        <div className="settings-fields">
          <div className="settings-field">
            <label htmlFor={`${placeholder}-price`}>Precio (opcional)</label>
            <input id={`${placeholder}-price`} type="text" value={price} disabled={pending} onChange={(event) => setPrice(event.target.value)} />
          </div>
          <div className="settings-field">
            <label htmlFor={`${placeholder}-currency`}>Moneda (ej. USD)</label>
            <input id={`${placeholder}-currency`} type="text" value={currency} disabled={pending} onChange={(event) => setCurrency(event.target.value)} />
          </div>
        </div>
      )}
      {pairInvalid && <p className="muted small-text">Precio y moneda deben indicarse juntos, o ambos vacíos.</p>}
      <div className="settings-form-actions" style={{ marginTop: 8 }}>
        <button type="button" className="button primary" disabled={!canSubmit} onClick={() => onSubmit(statement.trim(), price.trim(), currency.trim())}>
          Confirmar reemplazo
        </button>
        <button type="button" className="button" disabled={pending} onClick={onCancel}>
          Cancelar
        </button>
      </div>
    </div>
  );
}

function ObjectiveCard({
  objective,
  pending,
  onSupersede,
}: {
  objective: CommercialObjectivePublic;
  pending: boolean;
  onSupersede: (id: string, statement: string) => void;
}) {
  const [replacing, setReplacing] = useState(false);
  return (
    <article className="panel deliverable-card">
      <span className="deliverable-icon">
        <Icon name="campaign" size={19} />
      </span>
      <div>
        <h3>{objective.statement}</h3>
        <p className="muted small-text">
          {objective.current ? "Vigente" : "Histórico"} · Creado {formatDate(objective.created_at)}
        </p>
        {!objective.current && objective.superseded_by_commercial_objective_id && (
          <p className="muted small-text">Reemplazado por {objective.superseded_by_commercial_objective_id}</p>
        )}
        {objective.current && !replacing && (
          <div className="settings-form-actions" style={{ marginTop: 8 }}>
            <button type="button" className="button" disabled={pending} onClick={() => setReplacing(true)}>
              Reemplazar este objetivo
            </button>
          </div>
        )}
        {replacing && (
          <ReplaceForm
            pending={pending}
            placeholder={`objective-${objective.id}`}
            onCancel={() => setReplacing(false)}
            onSubmit={(statement) => {
              onSupersede(objective.id, statement);
              setReplacing(false);
            }}
          />
        )}
      </div>
    </article>
  );
}

function OfferCard({
  offer,
  pending,
  onSupersede,
}: {
  offer: OfferPublic;
  pending: boolean;
  onSupersede: (id: string, statement: string, price: string, currency: string) => void;
}) {
  const [replacing, setReplacing] = useState(false);
  return (
    <article className="panel deliverable-card">
      <span className="deliverable-icon">
        <Icon name="campaign" size={19} />
      </span>
      <div>
        <h3>{offer.statement}</h3>
        <p className="muted small-text">{formatPrice(offer.price, offer.currency)}</p>
        <p className="muted small-text">
          {offer.current ? "Vigente" : "Histórico"} · Creado {formatDate(offer.created_at)}
        </p>
        {!offer.current && offer.superseded_by_offer_id && (
          <p className="muted small-text">Reemplazado por {offer.superseded_by_offer_id}</p>
        )}
        {offer.current && !replacing && (
          <div className="settings-form-actions" style={{ marginTop: 8 }}>
            <button type="button" className="button" disabled={pending} onClick={() => setReplacing(true)}>
              Reemplazar esta oferta
            </button>
          </div>
        )}
        {replacing && (
          <ReplaceForm
            pending={pending}
            placeholder={`offer-${offer.id}`}
            offerFields
            onCancel={() => setReplacing(false)}
            onSubmit={(statement, price, currency) => {
              onSupersede(offer.id, statement, price, currency);
              setReplacing(false);
            }}
          />
        )}
      </div>
    </article>
  );
}

// MVP-36 (frozen by MVP-36A/-R1): "Resultados comerciales" — a governed,
// human-reported record of one realized commercial/business EVENT, never
// a period rollup. Every mutation goes through the real, authenticated,
// CSRF-protected backend routes (apps/api/app/commercial/router.py).
//
// CORE SEMANTIC (frozen, MVP-36A §G, the Attribution Firewall): an
// optional linked Distribution means ONLY that the outcome was
// observationally recorded in association with it — never that the
// Distribution (or any content/experiment it traces back to) caused,
// generated, or is responsible for the outcome. No causal wording
// anywhere in this section.
//
// "Registrar" (independent create) and "Corregir" (FULL-STATE, TIP-ONLY
// correction) are two distinct, never-conflated actions — a correction
// always creates a fresh successor row and never edits an existing row
// in place. content_distribution_id is never editable through a
// correction (backend structurally rejects it as an unknown field).

// MVP-36B-R1: UX-only mirror of the backend's numeric contract (the backend
// stays authoritative). quantity is a positive PostgreSQL int4; the value is
// Numeric(12,4) — non-negative, at most 8 integer digits and 4 decimals,
// i.e. 0 <= value <= 99999999.9999.
const OUTCOME_QUANTITY_MAX = 2147483647;
const OUTCOME_MONETARY_VALUE_PATTERN = /^\d{1,8}(\.\d{1,4})?$/;
// MVP-36B-R2/R3: the backend accepts occurred_at only between these two UTC
// instants — eight whole calendar days inside Python's year 1..9999 range, so
// the stored value stays readable under ANY PostgreSQL session timezone,
// including numeric/POSIX zones with offsets up to +168:59:00. The datetime
// input can be typed into year 0001 or 9999 — do not submit what the backend
// will reject. UX only; the backend stays authoritative.
const OUTCOME_OCCURRED_AT_MIN_MS = Date.parse("0001-01-09T00:00:00Z");
const OUTCOME_OCCURRED_AT_MAX_MS = Date.parse("9999-12-23T23:59:59.999Z");

function toIsoDateTime(localValue: string): string {
  const parsed = new Date(localValue);
  return Number.isNaN(parsed.getTime()) ? localValue : parsed.toISOString();
}

function toDatetimeLocalValue(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}T${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

function formatOutcomeMoney(value: string | null, currency: string | null): string | null {
  if (value === null || currency === null) return null;
  return `${value} ${currency}`;
}

function CommercialOutcomeForm({
  campaignId,
  target,
  onCancel,
  onSaved,
}: {
  campaignId: string;
  // Present only for a correction: the current effective Outcome being replaced.
  target?: CommercialOutcomePublic;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const isCorrection = target !== undefined;
  const [outcomeType, setOutcomeType] = useState(target?.outcome_type ?? "");
  const [quantity, setQuantity] = useState(target?.quantity != null ? String(target.quantity) : "");
  const [monetaryValue, setMonetaryValue] = useState(target?.monetary_value ?? "");
  const [currency, setCurrency] = useState(target?.currency ?? "");
  const [occurredAt, setOccurredAt] = useState(target ? toDatetimeLocalValue(target.occurred_at) : "");
  const [distributionId, setDistributionId] = useState("");
  const [externalReference, setExternalReference] = useState(target?.external_reference ?? "");
  const [correctionReason, setCorrectionReason] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [submitError, setSubmitError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  // One stable id per logical submission, reused across retries of the
  // same unresolved attempt, regenerated only after a confirmed success —
  // mirrors ContentDistributionEvidenceSection's own idempotency-key
  // discipline exactly.
  const clientRequestIdRef = useRef(crypto.randomUUID());

  const moneyPairInvalid = monetaryValue.trim().length > 0 !== currency.trim().length > 0;

  function validate(): Record<string, string> {
    const errors: Record<string, string> = {};
    if (!outcomeType.trim()) errors.outcomeType = "Ingresa el tipo de resultado.";
    if (!occurredAt) {
      errors.occurredAt = "Ingresa la fecha en que ocurrió.";
    } else {
      const occurredAtMs = new Date(occurredAt).getTime();
      if (Number.isNaN(occurredAtMs) || occurredAtMs < OUTCOME_OCCURRED_AT_MIN_MS || occurredAtMs > OUTCOME_OCCURRED_AT_MAX_MS) {
        errors.occurredAt = "Ingresa una fecha válida entre el 9 de enero de 0001 y el 23 de diciembre de 9999.";
      }
    }
    if (
      quantity.trim() &&
      (!/^\d+$/.test(quantity.trim()) || Number(quantity) < 1 || Number(quantity) > OUTCOME_QUANTITY_MAX)
    ) {
      errors.quantity = `Ingresa un número entero entre 1 y ${OUTCOME_QUANTITY_MAX}, o déjalo vacío.`;
    }
    if (moneyPairInvalid) {
      errors.monetaryValue = "Valor y moneda deben indicarse juntos, o ambos vacíos.";
    } else if (monetaryValue.trim() && !OUTCOME_MONETARY_VALUE_PATTERN.test(monetaryValue.trim())) {
      errors.monetaryValue = "Ingresa un valor entre 0 y 99999999.9999, con máximo 4 decimales.";
    }
    if (isCorrection && !correctionReason.trim()) errors.correctionReason = "Explica por qué corriges este registro.";
    return errors;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setSubmitError("");
    const errors = validate();
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    setSubmitting(true);
    try {
      if (target) {
        await correctCommercialOutcome(campaignId, target.id, {
          outcome_type: outcomeType.trim(),
          quantity: quantity.trim() ? Number(quantity) : null,
          monetary_value: monetaryValue.trim() || null,
          currency: currency.trim() || null,
          occurred_at: toIsoDateTime(occurredAt),
          external_reference: externalReference.trim() || null,
          client_request_id: clientRequestIdRef.current,
          correction_reason: correctionReason.trim(),
        });
      } else {
        await createCommercialOutcome(campaignId, {
          outcome_type: outcomeType.trim(),
          quantity: quantity.trim() ? Number(quantity) : null,
          monetary_value: monetaryValue.trim() || null,
          currency: currency.trim() || null,
          occurred_at: toIsoDateTime(occurredAt),
          content_distribution_id: distributionId.trim() || null,
          external_reference: externalReference.trim() || null,
          client_request_id: clientRequestIdRef.current,
        });
      }
      // Confirmed success: safe to start a fresh idempotency key.
      clientRequestIdRef.current = crypto.randomUUID();
      onSaved();
    } catch (error) {
      // Ambiguous failure: form values and the idempotency key are both
      // retained untouched, so a retry of this same submission reuses it.
      setSubmitError(describeCampaignError(error));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="panel" onSubmit={handleSubmit} noValidate style={{ marginTop: 12 }}>
      <div className="section-heading">
        <h4>{isCorrection ? "Corregir resultado comercial" : "Registrar resultado comercial"}</h4>
      </div>

      <div className="settings-fields">
        <div className="settings-field">
          <label htmlFor="outcome-type">Tipo de resultado</label>
          <input
            id="outcome-type"
            type="text"
            placeholder="Ej. lead, compra"
            value={outcomeType}
            disabled={submitting}
            onChange={(event) => setOutcomeType(event.target.value)}
          />
          {fieldErrors.outcomeType && <p className="small-text" role="alert">{fieldErrors.outcomeType}</p>}
        </div>
        <div className="settings-field">
          <label htmlFor="outcome-occurred-at">Fecha en que ocurrió</label>
          <input
            id="outcome-occurred-at"
            type="datetime-local"
            value={occurredAt}
            disabled={submitting}
            onChange={(event) => setOccurredAt(event.target.value)}
          />
          {fieldErrors.occurredAt && <p className="small-text" role="alert">{fieldErrors.occurredAt}</p>}
        </div>
      </div>

      <div className="settings-fields" style={{ marginTop: 12 }}>
        <div className="settings-field">
          <label htmlFor="outcome-quantity">Cantidad (opcional)</label>
          <input
            id="outcome-quantity"
            type="text"
            inputMode="numeric"
            value={quantity}
            disabled={submitting}
            onChange={(event) => setQuantity(event.target.value)}
          />
          {fieldErrors.quantity && <p className="small-text" role="alert">{fieldErrors.quantity}</p>}
        </div>
        <div className="settings-field">
          <label htmlFor="outcome-monetary-value">Valor (opcional)</label>
          <input
            id="outcome-monetary-value"
            type="text"
            value={monetaryValue}
            disabled={submitting}
            onChange={(event) => setMonetaryValue(event.target.value)}
          />
        </div>
        <div className="settings-field">
          <label htmlFor="outcome-currency">Moneda (ej. USD)</label>
          <input
            id="outcome-currency"
            type="text"
            value={currency}
            disabled={submitting}
            onChange={(event) => setCurrency(event.target.value)}
          />
        </div>
      </div>
      {fieldErrors.monetaryValue && <p className="small-text" role="alert">{fieldErrors.monetaryValue}</p>}

      {!isCorrection && (
        <div className="settings-field" style={{ marginTop: 12 }}>
          <label htmlFor="outcome-distribution-id">Distribución asociada (opcional)</label>
          <input
            id="outcome-distribution-id"
            type="text"
            placeholder="Ej. DST-XXXXXXXX"
            value={distributionId}
            disabled={submitting}
            onChange={(event) => setDistributionId(event.target.value)}
          />
          <p className="muted small-text">
            Asociación observacional únicamente — nunca implica que esa distribución generó o causó este resultado.
          </p>
        </div>
      )}
      {isCorrection && target?.content_distribution_id && (
        <p className="muted small-text" style={{ marginTop: 12 }}>
          Asociado con la distribución {target.content_distribution_id} (no editable en una corrección).
        </p>
      )}

      <div className="settings-field" style={{ marginTop: 12 }}>
        <label htmlFor="outcome-external-reference">Referencia externa (opcional)</label>
        <input
          id="outcome-external-reference"
          type="text"
          maxLength={2048}
          value={externalReference}
          disabled={submitting}
          onChange={(event) => setExternalReference(event.target.value)}
        />
      </div>

      {isCorrection && (
        <div className="settings-field" style={{ marginTop: 12 }}>
          <label htmlFor="outcome-correction-reason">Motivo de la corrección</label>
          <input
            id="outcome-correction-reason"
            type="text"
            maxLength={2000}
            value={correctionReason}
            disabled={submitting}
            onChange={(event) => setCorrectionReason(event.target.value)}
          />
          {fieldErrors.correctionReason && <p className="small-text" role="alert">{fieldErrors.correctionReason}</p>}
        </div>
      )}

      {submitError && (
        <p className="small-text" role="alert" style={{ marginTop: 16 }}>
          {submitError}
        </p>
      )}

      <div className="settings-form-actions" style={{ marginTop: 16 }}>
        <button type="submit" className="button primary" disabled={submitting}>
          {submitting ? "Guardando…" : isCorrection ? "Guardar corrección" : "Registrar resultado"}
        </button>
        <button type="button" className="button" onClick={onCancel} disabled={submitting}>
          Cancelar
        </button>
      </div>
    </form>
  );
}

function CommercialOutcomeCard({
  outcome,
  onCorrect,
  correcting,
}: {
  outcome: CommercialOutcomePublic;
  onCorrect: () => void;
  correcting: boolean;
}) {
  const money = formatOutcomeMoney(outcome.monetary_value, outcome.currency);
  return (
    <article className="panel deliverable-card">
      <span className="deliverable-icon">
        <Icon name="campaign" size={19} />
      </span>
      <div>
        <h3>{outcome.outcome_type}</h3>
        {outcome.quantity !== null && <p className="muted small-text">Cantidad: {outcome.quantity}</p>}
        {money && <p className="muted small-text">Valor: {money}</p>}
        <p className="muted small-text">Ocurrió el {formatDate(outcome.occurred_at)}</p>
        {outcome.content_distribution_id && (
          <p className="muted small-text">Asociado observacionalmente con la distribución {outcome.content_distribution_id}</p>
        )}
        {outcome.external_reference && <p className="muted small-text">Referencia: {outcome.external_reference}</p>}
        <p className="muted small-text">{outcome.is_current ? "Vigente" : "Histórico"}</p>
        {outcome.correction_reason && <p className="muted small-text">Motivo de corrección: {outcome.correction_reason}</p>}
        {outcome.is_current && !correcting && (
          <div className="settings-form-actions" style={{ marginTop: 8 }}>
            <button type="button" className="button" onClick={onCorrect}>
              Corregir
            </button>
          </div>
        )}
      </div>
    </article>
  );
}

export function CommercialPanel({
  campaignId,
  active,
  refreshToken,
}: {
  campaignId: string;
  active: boolean;
  refreshToken: number;
}) {
  const [result, setResult] = useState<Result | null>(null);
  const [pending, setPending] = useState(false);
  const [mutationError, setMutationError] = useState("");
  const [newObjective, setNewObjective] = useState("");
  const [newOfferStatement, setNewOfferStatement] = useState("");
  const [newOfferPrice, setNewOfferPrice] = useState("");
  const [newOfferCurrency, setNewOfferCurrency] = useState("");
  const [showOutcomeForm, setShowOutcomeForm] = useState(false);
  const [correctingOutcomeId, setCorrectingOutcomeId] = useState<string | null>(null);
  const requestedTokenRef = useRef<number | null>(null);

  const load = useCallback(
    (currentToken: number) => {
      requestedTokenRef.current = currentToken;
      Promise.all([getCommercialObjectives(campaignId), getOffers(campaignId), getCommercialOutcomes(campaignId)])
        .then(([objectives, offers, outcomes]) => setResult({ token: currentToken, status: "ready", objectives, offers, outcomes }))
        .catch((error) => {
          requestedTokenRef.current = null;
          setResult({ token: currentToken, status: "error", message: describeCampaignError(error) });
        });
    },
    [campaignId],
  );

  useEffect(() => {
    if (!active || requestedTokenRef.current === refreshToken) return;
    load(refreshToken);
  }, [active, refreshToken, load]);

  async function runMutation(currentToken: number, action: () => Promise<unknown>) {
    if (pending) return;
    setPending(true);
    setMutationError("");
    try {
      await action();
      load(currentToken);
    } catch (error) {
      setMutationError(describeCampaignError(error));
    } finally {
      setPending(false);
    }
  }

  const loading = result === null || result.token !== refreshToken;

  if (loading) {
    return (
      <section className="panel">
        <p className="muted small-text" role="status">
          Cargando definición comercial…
        </p>
      </section>
    );
  }

  if (result.status === "error") {
    return (
      <section className="panel workspace-empty">
        <span className="workspace-empty-symbol">
          <Icon name="campaign" size={32} />
        </span>
        <h2>No pudimos cargar la definición comercial en este momento.</h2>
        <p>
          {result.message}{" "}
          <button type="button" className="auth-text-button" onClick={() => load(refreshToken)}>
            Reintentar
          </button>
        </p>
      </section>
    );
  }

  const currentToken = result.token;
  const currentObjectives = result.objectives.filter((o) => o.current);
  const historicalObjectives = result.objectives.filter((o) => !o.current);
  const currentOffers = result.offers.filter((o) => o.current);
  const historicalOffers = result.offers.filter((o) => !o.current);
  const offerPairInvalid = newOfferPrice.trim().length > 0 !== newOfferCurrency.trim().length > 0;
  const currentOutcomes = result.outcomes.filter((o) => o.is_current);
  const historicalOutcomes = result.outcomes.filter((o) => !o.is_current);

  return (
    <>
      <section className="panel">
        <div className="section-heading">
          <h2>Objetivos comerciales</h2>
        </div>
        <p className="muted small-text">
          Lo que esta campaña busca lograr comercialmente. Puede haber varios objetivos vigentes a la vez — ninguno
          es &quot;principal&quot;.
        </p>
        {currentObjectives.length === 0 ? (
          <p className="muted small-text">Aún no se ha definido un objetivo comercial para esta campaña.</p>
        ) : (
          <div className="deliverables-grid" style={{ marginTop: 12 }}>
            {currentObjectives.map((objective) => (
              <ObjectiveCard
                key={objective.id}
                objective={objective}
                pending={pending}
                onSupersede={(id, statement) =>
                  runMutation(currentToken, () => supersedeCommercialObjective(campaignId, id, statement))
                }
              />
            ))}
          </div>
        )}
        <div className="panel" style={{ marginTop: 12 }}>
          <div className="settings-field">
            <label htmlFor="new-commercial-objective">Agregar otro objetivo</label>
            <textarea
              id="new-commercial-objective"
              value={newObjective}
              disabled={pending}
              onChange={(event) => setNewObjective(event.target.value)}
            />
          </div>
          <div className="settings-form-actions" style={{ marginTop: 8 }}>
            <button
              type="button"
              className="button primary"
              disabled={pending || newObjective.trim().length === 0}
              onClick={() =>
                runMutation(currentToken, () => createCommercialObjective(campaignId, newObjective.trim())).then(() =>
                  setNewObjective(""),
                )
              }
            >
              Agregar objetivo
            </button>
          </div>
        </div>
        {historicalObjectives.length > 0 && (
          <details style={{ marginTop: 12 }}>
            <summary>Historial de objetivos ({historicalObjectives.length})</summary>
            <div className="deliverables-grid" style={{ marginTop: 8 }}>
              {historicalObjectives.map((objective) => (
                <article key={objective.id} className="panel deliverable-card">
                  <div>
                    <h3>{objective.statement}</h3>
                    <p className="muted small-text">Reemplazado el {objective.superseded_at ? formatDate(objective.superseded_at) : ""}</p>
                  </div>
                </article>
              ))}
            </div>
          </details>
        )}
      </section>

      <section className="panel" style={{ marginTop: 24 }}>
        <div className="section-heading">
          <h2>Ofertas</h2>
        </div>
        <p className="muted small-text">
          Lo que esta campaña ofrece comercialmente. Puede haber varias ofertas vigentes a la vez (por ejemplo, dos
          combinaciones de producto y precio en prueba).
        </p>
        {currentOffers.length === 0 ? (
          <p className="muted small-text">Aún no se ha definido una oferta para esta campaña.</p>
        ) : (
          <div className="deliverables-grid" style={{ marginTop: 12 }}>
            {currentOffers.map((offer) => (
              <OfferCard
                key={offer.id}
                offer={offer}
                pending={pending}
                onSupersede={(id, statement, price, currency) =>
                  runMutation(currentToken, () =>
                    supersedeOffer(campaignId, id, statement, price.length ? price : null, currency.length ? currency : null),
                  )
                }
              />
            ))}
          </div>
        )}
        <div className="panel" style={{ marginTop: 12 }}>
          <div className="settings-field">
            <label htmlFor="new-offer-statement">Agregar otra oferta</label>
            <textarea
              id="new-offer-statement"
              value={newOfferStatement}
              disabled={pending}
              onChange={(event) => setNewOfferStatement(event.target.value)}
            />
          </div>
          <div className="settings-fields">
            <div className="settings-field">
              <label htmlFor="new-offer-price">Precio (opcional)</label>
              <input
                id="new-offer-price"
                type="text"
                value={newOfferPrice}
                disabled={pending}
                onChange={(event) => setNewOfferPrice(event.target.value)}
              />
            </div>
            <div className="settings-field">
              <label htmlFor="new-offer-currency">Moneda (ej. USD)</label>
              <input
                id="new-offer-currency"
                type="text"
                value={newOfferCurrency}
                disabled={pending}
                onChange={(event) => setNewOfferCurrency(event.target.value)}
              />
            </div>
          </div>
          {offerPairInvalid && <p className="muted small-text">Precio y moneda deben indicarse juntos, o ambos vacíos.</p>}
          <div className="settings-form-actions" style={{ marginTop: 8 }}>
            <button
              type="button"
              className="button primary"
              disabled={pending || newOfferStatement.trim().length === 0 || offerPairInvalid}
              onClick={() =>
                runMutation(currentToken, () =>
                  createOffer(
                    campaignId,
                    newOfferStatement.trim(),
                    newOfferPrice.trim().length ? newOfferPrice.trim() : null,
                    newOfferCurrency.trim().length ? newOfferCurrency.trim() : null,
                  ),
                ).then(() => {
                  setNewOfferStatement("");
                  setNewOfferPrice("");
                  setNewOfferCurrency("");
                })
              }
            >
              Agregar oferta
            </button>
          </div>
        </div>
        {historicalOffers.length > 0 && (
          <details style={{ marginTop: 12 }}>
            <summary>Historial de ofertas ({historicalOffers.length})</summary>
            <div className="deliverables-grid" style={{ marginTop: 8 }}>
              {historicalOffers.map((offer) => (
                <article key={offer.id} className="panel deliverable-card">
                  <div>
                    <h3>{offer.statement}</h3>
                    <p className="muted small-text">{formatPrice(offer.price, offer.currency)}</p>
                    <p className="muted small-text">Reemplazada el {offer.superseded_at ? formatDate(offer.superseded_at) : ""}</p>
                  </div>
                </article>
              ))}
            </div>
          </details>
        )}
      </section>

      <section className="panel" style={{ marginTop: 24 }}>
        <div className="section-heading">
          <h2>Resultados comerciales</h2>
        </div>
        <p className="muted small-text">
          Eventos comerciales reales registrados manualmente (por ejemplo, un lead o una compra). Un registro asociado
          con una distribución significa solo que ocurrió en asociación observacional con ella — nunca que esa
          distribución lo generó o causó.
        </p>

        {currentOutcomes.length === 0 ? (
          <p className="muted small-text">Aún no se ha registrado un resultado comercial para esta campaña.</p>
        ) : (
          <div className="deliverables-grid" style={{ marginTop: 12 }}>
            {currentOutcomes.map((outcome) =>
              correctingOutcomeId === outcome.id ? (
                <CommercialOutcomeForm
                  key={outcome.id}
                  campaignId={campaignId}
                  target={outcome}
                  onCancel={() => setCorrectingOutcomeId(null)}
                  onSaved={() => {
                    setCorrectingOutcomeId(null);
                    load(currentToken);
                  }}
                />
              ) : (
                <CommercialOutcomeCard
                  key={outcome.id}
                  outcome={outcome}
                  correcting={correctingOutcomeId !== null}
                  onCorrect={() => {
                    setShowOutcomeForm(false);
                    setCorrectingOutcomeId(outcome.id);
                  }}
                />
              ),
            )}
          </div>
        )}

        {!showOutcomeForm && correctingOutcomeId === null && (
          <div className="settings-form-actions" style={{ marginTop: 12 }}>
            <button type="button" className="button primary" onClick={() => setShowOutcomeForm(true)}>
              Registrar resultado comercial
            </button>
          </div>
        )}

        {showOutcomeForm && (
          <CommercialOutcomeForm
            campaignId={campaignId}
            onCancel={() => setShowOutcomeForm(false)}
            onSaved={() => {
              setShowOutcomeForm(false);
              load(currentToken);
            }}
          />
        )}

        {historicalOutcomes.length > 0 && (
          <details style={{ marginTop: 12 }}>
            <summary>Historial de resultados comerciales ({historicalOutcomes.length})</summary>
            <div className="deliverables-grid" style={{ marginTop: 8 }}>
              {historicalOutcomes.map((outcome) => (
                <article key={outcome.id} className="panel deliverable-card">
                  <div>
                    <h3>{outcome.outcome_type}</h3>
                    <p className="muted small-text">{formatOutcomeMoney(outcome.monetary_value, outcome.currency)}</p>
                    <p className="muted small-text">Ocurrió el {formatDate(outcome.occurred_at)}</p>
                    {outcome.correction_reason && <p className="muted small-text">Motivo de corrección: {outcome.correction_reason}</p>}
                  </div>
                </article>
              ))}
            </div>
          </details>
        )}
      </section>

      {mutationError && (
        <p role="alert" className="settings-feedback">
          {mutationError}
        </p>
      )}
    </>
  );
}
