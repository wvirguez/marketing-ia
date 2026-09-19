"""Governed Experiment Definition — MVP-37 (frozen MVP-37A/-37B).

``ExperimentDefinitionService.write_version`` is the SINGLE writer of
``experiment_definition_versions`` — the choke point of the Definition lock
("once any governed pinning child pins the tip, no further version may be
written"), implemented in MVP-38 with Variant as the first trigger.

Write order (frozen MVP-37B §U): resolve the campaign-scoped Experiment →
fast-path replay lookup → lock the Experiment's Strategy row → lock the
Experiment row (canonical order: Strategy, then Experiment; no other path
locks an Experiment row, so it is a leaf and the order is acyclic against
``StrategyRevisionService.revise_strategy``'s Decision-then-Strategy order)
→ re-check replay under the locks → verify the Strategy is still current →
verify ``base_version`` equals the tip → reject an unchanged revision →
insert + audit → commit. The DEFINITION LOCK (MVP-38) sits after the base-version check and before
the unchanged check: once a governed pinning child (today only a Variant,
via ``_has_pinning_children``) pins the tip, no new version may be written
(``EXPERIMENT_DEFINITION_PINNED``). The lock is derived from the existence
of a pinning child — never stored — and Experiment Definition governance,
not Variant, owns it; future pinning children add themselves to the single
``_has_pinning_children`` seam. ``UNIQUE(experiment_id, version)`` and
``UNIQUE(workspace_id, client_request_id)`` are the structural backstops;
only those two known violations are translated, anything else re-raises.

A matching replay of an already-committed write returns 200 without any new
row or audit event, even if the Strategy has since been superseded — it is
not a new write.

DECLARATION != PRE-REGISTRATION. DEFINITION != VALID EXPERIMENT. Nothing
here transitions Experiment/Hypothesis status or writes any other table.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign
from app.core.api_errors import (
    ExperimentDefinitionBaseStaleError,
    ExperimentDefinitionPinnedError,
    ExperimentDefinitionStrategyStaleError,
    ExperimentDefinitionUnchangedError,
    ForbiddenError,
    IdempotencyKeyConflictError,
)
from app.strategy.models import Experiment, ExperimentDefinitionVersion
from app.strategy.repository import (
    ExperimentDefinitionRepository,
    ExperimentRepository,
    ExperimentVariantRepository,
    HypothesisRepository,
    StrategyRepository,
)

EVENT_EXPERIMENT_DEFINITION_DECLARED = "strategy.experiment_definition.declared"
EVENT_EXPERIMENT_DEFINITION_REVISED = "strategy.experiment_definition.revised"

_UQ_EXPERIMENT_VERSION = "uq_experiment_definition_versions_experiment_version"
_UQ_CLIENT_REQUEST_ID = "uq_experiment_definition_versions_workspace_client_request_id"

DEFINITION_FIELDS = (
    "comparison_question",
    "comparison_type",
    "changed_factor",
    "controlled_factors",
    "comparison_basis",
    "scope",
    "learning_intent",
    "non_conclusion_boundary",
)


def _content_equal(row: ExperimentDefinitionVersion, fields: dict) -> bool:
    """Exact, case-sensitive, order-sensitive equality over the eight
    normalized Definition fields (frozen MVP-37B §W)."""
    return all(getattr(row, name) == fields[name] for name in DEFINITION_FIELDS)


class ExperimentDefinitionService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.strategies = StrategyRepository(session)
        self.hypotheses = HypothesisRepository(session)
        self.experiments = ExperimentRepository(session)
        self.definitions = ExperimentDefinitionRepository(session)
        self.variants = ExperimentVariantRepository(session)
        self.events = AuditEventRepository(session)

    # --- definition lock (derived) ---------------------------------------

    def _has_pinning_children(self, *, definition_version_id: uuid.UUID) -> bool:
        """THE central Definition-lock seam (MVP-38B §E/§H). Must be called
        with the Strategy and Experiment row locks already held. Today the
        only pinning child is a Variant; a future pinning child (e.g. a
        measurement contract) adds its own existence check HERE rather than
        inventing separate lock semantics."""
        return self.variants.exists_for_definition_version(definition_version_id=definition_version_id)

    def pin_states_for_versions(
        self, *, workspace_id: uuid.UUID, definition_version_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[int, bool]]:
        """``{version_id: (variant_count, is_pinned)}`` — derived, batched
        (one grouped query), never stored. ``is_pinned`` means only that a
        governed pinning child exists; for MVP-38 it equals
        ``variant_count > 0`` but is computed from the set of pinning
        children so future children participate without changing
        ``variant_count``."""
        variant_counts = self.variants.counts_for_versions(
            definition_version_ids=definition_version_ids, workspace_id=workspace_id
        )
        return {
            version_id: (variant_counts.get(version_id, 0), variant_counts.get(version_id, 0) > 0)
            for version_id in definition_version_ids
        }

    # --- reads -----------------------------------------------------------

    def tips_for_experiments(
        self, *, workspace_id: uuid.UUID, experiments: list[Experiment]
    ) -> dict[uuid.UUID, ExperimentDefinitionVersion]:
        return self.definitions.tips_for_experiments(
            experiment_ids=[experiment.id for experiment in experiments], workspace_id=workspace_id
        )

    def get_history(
        self, *, campaign: Campaign, experiment_public_id: str
    ) -> tuple[Experiment, list[ExperimentDefinitionVersion]]:
        """Readable for ANY Experiment of the campaign, including one under a
        superseded Strategy. Non-leaky: an unknown/cross-campaign/
        cross-workspace Experiment is the same ``ForbiddenError``."""
        experiment = self._resolve_experiment(campaign=campaign, experiment_public_id=experiment_public_id)
        versions = self.definitions.list_for_experiment(
            experiment_id=experiment.id, workspace_id=campaign.workspace_id
        )
        return experiment, versions

    # --- write (the single writer) ---------------------------------------

    def write_version(
        self,
        *,
        campaign: Campaign,
        experiment_public_id: str,
        base_version: int,
        client_request_id: str,
        fields: dict,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> tuple[ExperimentDefinitionVersion, bool]:
        """Returns ``(version, created)``; ``created`` is False for a matching
        replay. Raises ``IdempotencyKeyConflictError`` when the key was used
        for a materially different request."""
        experiment = self._resolve_experiment(campaign=campaign, experiment_public_id=experiment_public_id)
        # Plain identifiers, captured before any rollback can expire the ORM objects.
        experiment_id = experiment.id
        workspace_id = campaign.workspace_id
        campaign_id = campaign.id
        hypothesis_id = experiment.hypothesis_id

        replay = self._replay(
            workspace_id=workspace_id,
            experiment_id=experiment_id,
            base_version=base_version,
            client_request_id=client_request_id,
            fields=fields,
        )
        if replay is not None:
            return replay, False

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
            base_version=base_version,
            client_request_id=client_request_id,
            fields=fields,
        )
        if replay is not None:
            return replay, False

        current_strategy = self.strategies.get_current_for_campaign(campaign_id)
        if current_strategy is None or current_strategy.id != strategy_id:
            raise ExperimentDefinitionStrategyStaleError()

        tip = self.definitions.get_tip(experiment_id=experiment_id, workspace_id=workspace_id)
        tip_version = tip.version if tip is not None else 0
        if base_version != tip_version:
            raise ExperimentDefinitionBaseStaleError()
        if tip is not None and self._has_pinning_children(definition_version_id=tip.id):
            raise ExperimentDefinitionPinnedError()
        if tip is not None and _content_equal(tip, fields):
            raise ExperimentDefinitionUnchangedError()

        new_version = tip_version + 1
        try:
            row = self.definitions.create(
                experiment=locked_experiment,
                version=new_version,
                client_request_id=client_request_id,
                **fields,
            )
            self.events.record(
                workspace_id=workspace_id,
                event_type=(
                    EVENT_EXPERIMENT_DEFINITION_DECLARED if new_version == 1 else EVENT_EXPERIMENT_DEFINITION_REVISED
                ),
                actor_type=ActorType.USER,
                campaign_id=campaign_id,
                strategy_id=strategy_id,
                hypothesis_id=hypothesis_id,
                experiment_id=experiment_id,
                experiment_definition_version_id=row.id,
                previous_state=f"v{tip_version}" if tip_version else None,
                new_state=f"v{new_version}",
                actor_user_id=actor_user_id,
                request_id=request_id,
            )
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == _UQ_EXPERIMENT_VERSION:
                raise ExperimentDefinitionBaseStaleError() from None
            if constraint == _UQ_CLIENT_REQUEST_ID:
                replay = self._replay(
                    workspace_id=workspace_id,
                    experiment_id=experiment_id,
                    base_version=base_version,
                    client_request_id=client_request_id,
                    fields=fields,
                )
                if replay is not None:
                    return replay, False
            raise
        return row, True

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
        base_version: int,
        client_request_id: str,
        fields: dict,
    ) -> ExperimentDefinitionVersion | None:
        """None when the key is unused; the stored row when the key was used
        for a materially equal request (same Experiment, same base_version,
        same normalized eight fields); otherwise a key conflict."""
        existing = self.definitions.get_by_workspace_and_request_id(
            workspace_id=workspace_id, client_request_id=client_request_id
        )
        if existing is None:
            return None
        if (
            existing.experiment_id == experiment_id
            and existing.version - 1 == base_version
            and _content_equal(existing, fields)
        ):
            return existing
        raise IdempotencyKeyConflictError()
