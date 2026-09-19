"""Governed Variant Identity — MVP-38 (frozen MVP-38A/-38B).

``ExperimentVariantService.declare_variant`` is the SINGLE writer of
``experiment_variants``. A Variant is the immutable identity of ONE declared
condition of one Experiment, pinned to ONE immutable
``ExperimentDefinitionVersion`` (the current tip).

VARIANT IDENTITY != ALLOCATION != RANDOMIZATION != EXPOSURE != MEASUREMENT !=
RESULT != WINNER != CAUSALITY != EXECUTION AUTHORIZATION. Nothing here
writes any table other than ``experiment_variants`` and ``audit_events``,
transitions any status, or links to content, distribution, evidence or
commercial outcomes.

Write order (frozen MVP-38B §F): resolve the campaign-scoped Experiment →
resolve the requested ``EXD-…`` INSIDE that Experiment → fast-path replay
lookup → lock the Strategy row → lock the Experiment row (canonical order:
Strategy, then Experiment — the same order ``ExperimentDefinitionService``
uses, so the two writers serialize on the Experiment row and never form a
second lock order) → re-check replay under the locks → the Strategy must
still be current → the requested version must equal the re-read tip (never
silently rebased) → normalized duplicate-label check → ordinal = max + 1 →
insert + audit → commit. Because both this writer and
``ExperimentDefinitionService.write_version`` hold the Experiment row lock
and re-read the tip, ``Variant(N)`` and ``Definition(N+1)`` can never both
commit from the same N-tip race (MVP-38B §F).

Only the two known unique violations are translated (exact label →
``EXPERIMENT_VARIANT_LABEL_DUPLICATE``; workspace/client_request_id →
rollback, re-read, replay or ``IDEMPOTENCY_KEY_CONFLICT``). Any other
``IntegrityError`` — including an ordinal collision, which the Experiment
lock makes impossible — re-raises: it is an invariant failure, never a
normal domain result.

There is deliberately NO maximum number of Variants, and no correction,
supersession or retirement (MVP38A-OBS-3, a current capability limit).
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign
from app.core.api_errors import (
    ExperimentVariantDefinitionVersionNotCurrentError,
    ExperimentVariantLabelDuplicateError,
    ExperimentVariantStrategyStaleError,
    ForbiddenError,
    IdempotencyKeyConflictError,
)
from app.strategy.models import Experiment, ExperimentDefinitionVersion, ExperimentVariant
from app.strategy.repository import (
    ExperimentDefinitionRepository,
    ExperimentRepository,
    ExperimentVariantRepository,
    HypothesisRepository,
    StrategyRepository,
)

EVENT_EXPERIMENT_VARIANT_DECLARED = "strategy.variant.declared"

_UQ_LABEL = "uq_experiment_variants_definition_version_label"
_UQ_CLIENT_REQUEST_ID = "uq_experiment_variants_workspace_client_request_id"


def label_key(value: str) -> str:
    """Comparison key used ONLY to detect logically duplicate labels
    (frozen MVP-38B §J); never stored, never used to rewrite a label."""
    return " ".join(value.split()).casefold()


class ExperimentVariantService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.strategies = StrategyRepository(session)
        self.hypotheses = HypothesisRepository(session)
        self.experiments = ExperimentRepository(session)
        self.definitions = ExperimentDefinitionRepository(session)
        self.variants = ExperimentVariantRepository(session)
        self.events = AuditEventRepository(session)

    # --- reads -----------------------------------------------------------

    def list_variants(
        self, *, campaign: Campaign, experiment_public_id: str, limit: int, offset: int
    ) -> tuple[Experiment, list[tuple[ExperimentVariant, ExperimentDefinitionVersion]], int]:
        """Readable for ANY Experiment of the campaign, including one under a
        superseded Strategy; a definition-less Experiment yields an empty
        page. Non-leaky: unknown/cross-tenant is the same ``ForbiddenError``."""
        experiment = self._resolve_experiment(campaign=campaign, experiment_public_id=experiment_public_id)
        items = self.variants.list_for_experiment(
            experiment_id=experiment.id, workspace_id=campaign.workspace_id, limit=limit, offset=offset
        )
        total = self.variants.count_for_experiment(experiment_id=experiment.id, workspace_id=campaign.workspace_id)
        return experiment, items, total

    # --- write (the single writer) ---------------------------------------

    def declare_variant(
        self,
        *,
        campaign: Campaign,
        experiment_public_id: str,
        definition_version_public_id: str,
        label: str,
        condition_description: str,
        client_request_id: str,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> tuple[ExperimentVariant, ExperimentDefinitionVersion, bool]:
        """Returns ``(variant, pinned_version, created)``; ``created`` is
        False for a matching replay."""
        experiment = self._resolve_experiment(campaign=campaign, experiment_public_id=experiment_public_id)
        # Plain identifiers, captured before any rollback can expire the ORM objects.
        experiment_id = experiment.id
        workspace_id = campaign.workspace_id
        campaign_id = campaign.id
        hypothesis_id = experiment.hypothesis_id

        requested = self.definitions.get_by_public_id_for_experiment(
            experiment_id=experiment_id, workspace_id=workspace_id, public_id=definition_version_public_id
        )
        if requested is None:  # unknown / foreign / no definition declared: non-leaky
            raise ForbiddenError()
        requested_id = requested.id
        requested_version = requested.version

        replay = self._replay(
            workspace_id=workspace_id,
            experiment_id=experiment_id,
            definition_version_id=requested_id,
            label=label,
            condition_description=condition_description,
            client_request_id=client_request_id,
        )
        if replay is not None:
            return replay, requested, False

        hypothesis = self.hypotheses.get_by_id(hypothesis_id)
        if hypothesis is None:  # pragma: no cover - FK guarantees existence
            raise ForbiddenError()
        strategy_id = hypothesis.strategy_id

        # Canonical lock order: Strategy row, then Experiment row.
        strategy = self.strategies.get_by_id(strategy_id, for_update=True)
        if strategy is None:  # pragma: no cover - FK guarantees existence
            raise ForbiddenError()
        locked_experiment = self.experiments.get_by_id(experiment_id, for_update=True)
        if locked_experiment is None:  # pragma: no cover - FK guarantees existence
            raise ForbiddenError()

        replay = self._replay(
            workspace_id=workspace_id,
            experiment_id=experiment_id,
            definition_version_id=requested_id,
            label=label,
            condition_description=condition_description,
            client_request_id=client_request_id,
        )
        if replay is not None:
            return replay, requested, False

        current_strategy = self.strategies.get_current_for_campaign(campaign_id)
        if current_strategy is None or current_strategy.id != strategy_id:
            raise ExperimentVariantStrategyStaleError()

        tip = self.definitions.get_tip(experiment_id=experiment_id, workspace_id=workspace_id)
        if tip is None or tip.id != requested_id:
            raise ExperimentVariantDefinitionVersionNotCurrentError()

        new_key = label_key(label)
        if any(label_key(existing) == new_key for existing in self.variants.list_labels_for_version(
            definition_version_id=requested_id
        )):
            raise ExperimentVariantLabelDuplicateError()

        ordinal = self.variants.max_ordinal_for_version(definition_version_id=requested_id) + 1
        try:
            row = self.variants.create(
                experiment_id=experiment_id,
                workspace_id=workspace_id,
                definition_version_id=requested_id,
                ordinal=ordinal,
                label=label,
                condition_description=condition_description,
                client_request_id=client_request_id,
            )
            self.events.record(
                workspace_id=workspace_id,
                event_type=EVENT_EXPERIMENT_VARIANT_DECLARED,
                actor_type=ActorType.USER,
                campaign_id=campaign_id,
                strategy_id=strategy_id,
                hypothesis_id=hypothesis_id,
                experiment_id=experiment_id,
                experiment_definition_version_id=requested_id,
                experiment_variant_id=row.id,
                previous_state=None,
                new_state=f"pinned:v{requested_version}",
                actor_user_id=actor_user_id,
                request_id=request_id,
            )
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == _UQ_LABEL:
                raise ExperimentVariantLabelDuplicateError() from None
            if constraint == _UQ_CLIENT_REQUEST_ID:
                replay = self._replay(
                    workspace_id=workspace_id,
                    experiment_id=experiment_id,
                    definition_version_id=requested_id,
                    label=label,
                    condition_description=condition_description,
                    client_request_id=client_request_id,
                )
                if replay is not None:
                    return replay, requested, False
            raise
        return row, requested, True

    # --- internals ---------------------------------------------------------

    def _resolve_experiment(self, *, campaign: Campaign, experiment_public_id: str) -> Experiment:
        experiment = self.experiments.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=experiment_public_id
        )
        if experiment is None or experiment.workspace_id != campaign.workspace_id:
            raise ForbiddenError()
        return experiment

    def _replay(
        self,
        *,
        workspace_id: uuid.UUID,
        experiment_id: uuid.UUID,
        definition_version_id: uuid.UUID,
        label: str,
        condition_description: str,
        client_request_id: str,
    ) -> ExperimentVariant | None:
        """None when the key is unused; the stored row when the key was used
        for a materially equal request (same Experiment, same pinned version,
        same normalized label and description, compared exactly and
        case-sensitively); otherwise a key conflict."""
        existing = self.variants.get_by_workspace_and_request_id(
            workspace_id=workspace_id, client_request_id=client_request_id
        )
        if existing is None:
            return None
        if (
            existing.experiment_id == experiment_id
            and existing.definition_version_id == definition_version_id
            and existing.label == label
            and existing.condition_description == condition_description
        ):
            return existing
        raise IdempotencyKeyConflictError()
