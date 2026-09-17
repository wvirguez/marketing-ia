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

import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/icon";
import {
  createCommercialObjective,
  createOffer,
  getCommercialObjectives,
  getOffers,
  supersedeCommercialObjective,
  supersedeOffer,
} from "@/lib/api/commercial";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type { CommercialObjectivePublic, OfferPublic } from "@/types/commercial";

type Result =
  | { token: number; status: "error"; message: string }
  | { token: number; status: "ready"; objectives: CommercialObjectivePublic[]; offers: OfferPublic[] };

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
  const requestedTokenRef = useRef<number | null>(null);

  const load = useCallback(
    (currentToken: number) => {
      requestedTokenRef.current = currentToken;
      Promise.all([getCommercialObjectives(campaignId), getOffers(campaignId)])
        .then(([objectives, offers]) => setResult({ token: currentToken, status: "ready", objectives, offers }))
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

      {mutationError && (
        <p role="alert" className="settings-feedback">
          {mutationError}
        </p>
      )}
    </>
  );
}
