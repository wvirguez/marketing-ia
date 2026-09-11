"""MVP-04 deterministic orchestration bootstrap — pure content builders.

These functions synthesize DRAFT text/payloads for Research, Audience, and
Strategy, derived ONLY from the Campaign's name and its CampaignBrief fields
(prompt, product_type, price, audience, budget, channel). Planning
(``build_plan_content``) is the one exception (MVP-04R): it is derived from
the Campaign's name, its CampaignBrief, AND the persisted ``Positioning``
produced by the STRATEGY stage — Planning must express CampaignBrief ->
Strategy -> Plan, not CampaignBrief -> Plan directly. No external network
call, no LLM/provider call, no hidden fixture corpus, and no fabricated
company/competitor/customer fact.

Neither ResearchReport, AudienceProfile, Strategy, nor ContentPlan has a
structural maturity/status/"synthetic" column (confirmed in the MVP-04
Phase 1 architecture gate) — so every narrative field below explicitly
discloses, in its own text, that it is an initial draft derived only
from the Campaign Brief, never external research, never validated
evidence, never a strategic or content decision. This is the only
honesty mechanism available without inventing a schema field, which is
explicitly out of scope for this phase.

`OrchestrationService` (app/orchestration/service.py) owns state
transitions, provenance objects, call order, and failure behavior — this
module owns none of that and never touches the database.
"""

from __future__ import annotations

from app.campaigns.models import CampaignBrief
from app.strategy.models import Positioning

_DRAFT_DISCLAIMER = (
    "Borrador inicial generado automáticamente a partir del brief de la "
    "campaña. No proviene de investigación externa, no ha sido validado "
    "y no representa una decisión aprobada."
)


def _or_unspecified(value: str | None) -> str:
    return value if value else "no especificado"


def build_research_summary(*, campaign_name: str, brief: CampaignBrief) -> str:
    return (
        f"{_DRAFT_DISCLAIMER}\n\n"
        f"Campaña: {campaign_name}\n"
        f"Idea original (brief): {brief.prompt}\n"
        f"Tipo de producto: {_or_unspecified(brief.product_type)}\n"
        f"Precio de referencia: {_or_unspecified(brief.price)}\n"
        f"Canal principal: {_or_unspecified(brief.channel)}\n\n"
        "Próximo paso sugerido: validar estos supuestos con investigación de "
        "mercado real antes de tomar decisiones de estrategia."
    )


def build_audience_summary(*, campaign_name: str, brief: CampaignBrief) -> str:
    return (
        f"{_DRAFT_DISCLAIMER}\n\n"
        f"Campaña: {campaign_name}\n"
        f"Audiencia descrita en el brief: {_or_unspecified(brief.audience)}\n"
        f"Tipo de producto: {_or_unspecified(brief.product_type)}\n\n"
        "Este perfil no incluye evidencia de voz del cliente (VOC) real; debe "
        "completarse con datos de clientes reales antes de usarse como base "
        "de decisiones."
    )


def build_strategy_content(*, campaign_name: str, brief: CampaignBrief) -> dict:
    summary = (
        f"{_DRAFT_DISCLAIMER}\n\n"
        f"Campaña: {campaign_name}\n"
        f"Basado en la idea: {brief.prompt}\n"
        f"Presupuesto de referencia: {_or_unspecified(brief.budget)}\n\n"
        "Esta estrategia preliminar no representa una decisión estratégica "
        "aprobada."
    )
    positioning_statement = (
        f"Posicionamiento preliminar (borrador, sin validar) para "
        f"{_or_unspecified(brief.product_type)}, dirigido a "
        f"{_or_unspecified(brief.audience)}."
    )
    hypothesis_statement = (
        "Hipótesis inicial sin confirmar: comunicar con claridad "
        f"{_or_unspecified(brief.product_type)} a {_or_unspecified(brief.audience)} "
        "podría generar interés medible. Requiere validación con datos reales "
        "antes de confirmarse o descartarse."
    )
    # Zero experiments: the Strategy schema/service has no minimum-count
    # requirement (confirmed in the MVP-04 Phase 1 gate), so none are
    # invented here without review.
    return {
        "summary": summary,
        "positioning_statement": positioning_statement,
        "hypotheses": [{"statement": hypothesis_statement, "experiments": []}],
    }


def build_plan_content(
    *, campaign_name: str, brief: CampaignBrief, strategy_positioning: Positioning
) -> dict:
    """MVP-04R: Planning is no longer derived from CampaignBrief alone — it
    must incorporate the actual persisted Strategy output (the STRATEGY
    stage's ``Positioning.statement``), so this deterministic synthesis
    expresses CampaignBrief -> Strategy -> Plan rather than restating the
    brief. ``strategy_positioning`` must be the real persisted row for this
    Campaign's current Strategy — never independently reconstructed here."""
    positioning_statement = strategy_positioning.statement
    summary = (
        f"{_DRAFT_DISCLAIMER}\n\n"
        f"Campaña: {campaign_name}\n"
        "Plan de contenido preliminar; no representa un calendario de "
        "producción aprobado.\n\n"
        "Parte del posicionamiento definido en la Estrategia de esta "
        f'campaña: "{positioning_statement}"'
    )
    channel = _or_unspecified(brief.channel)
    items = [
        {
            "format": channel,
            "objective": (
                "Presentar la idea principal de la campaña de forma clara "
                "(borrador, pendiente de validar)."
            ),
            "sequence": 1,
            "scheduled_date": None,
        },
        {
            "format": channel,
            "objective": (
                "Comunicar el posicionamiento definido en la Estrategia "
                f'("{positioning_statement}") a '
                f"{_or_unspecified(brief.audience)} (borrador, pendiente de validar)."
            ),
            "sequence": 2,
            "scheduled_date": None,
        },
    ]
    return {"summary": summary, "items": items}
