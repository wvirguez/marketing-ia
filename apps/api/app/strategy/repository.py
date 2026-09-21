"""Data access for Strategy / Positioning / Hypothesis / Experiment. No
repository here calls ``session.commit()`` — see
``app/persistence/session.py`` and ``app/strategy/service.py`` for the
transaction-ownership boundary.

Every ``create``/``create_many`` method takes the parent domain object
(``Campaign``, ``CampaignRun``, ``RunStageExecution``, ``Strategy``,
``Hypothesis``), never a raw ``workspace_id``/``campaign_id``/
``strategy_id``/``hypothesis_id`` parameter — the same "no independent
parameter, no possibility of drift" pattern established in
``app/campaigns/repository.py``/``app/research/repository.py``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.campaigns.models import Campaign, CampaignRun
from app.core.ids import generate_public_id
from app.orchestration.models import RunStageExecution
from app.strategy.models import (
    Experiment,
    ExecutionAuthorization,
    ExecutionAuthorizationVariant,
    ExecutionStartAttestation,
    ExperimentDefinitionVersion,
    ExperimentEvidenceClaim,
    ExperimentVariant,
    Hypothesis,
    HypothesisStatus,
    MeasurementContractRequiredSignal,
    MeasurementContractVersion,
    Positioning,
    Strategy,
    StrategyOrigin,
)


class StrategyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        campaign: Campaign,
        version: int,
        summary: str,
        origin: StrategyOrigin,
        campaign_run: CampaignRun | None = None,
        stage_execution: RunStageExecution | None = None,
    ) -> Strategy:
        """``campaign_run``/``stage_execution`` are required together for
        ``origin=BOOTSTRAP`` and must be omitted together for
        ``origin=REVISION`` (MVP-30A-R1) — the DB's own
        ``ck_strategies_origin_bootstrap_fields`` CHECK constraint is the
        actual backstop, never trusted as merely an application-level
        convention (mirrors every other origin/status invariant in this
        codebase)."""
        strategy = Strategy(
            public_id=generate_public_id("STR"),
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.id,
            origin=origin,
            campaign_run_id=campaign_run.id if campaign_run is not None else None,
            stage_execution_id=stage_execution.id if stage_execution is not None else None,
            version=version,
            summary=summary,
        )
        self.session.add(strategy)
        self.session.flush()
        return strategy

    def get_by_public_id(self, public_id: str) -> Strategy | None:
        return self.session.execute(select(Strategy).where(Strategy.public_id == public_id)).scalar_one_or_none()

    def get_by_id(self, strategy_id: uuid.UUID, *, for_update: bool = False) -> Strategy | None:
        """MVP-32B: resolves a Strategy from an already-trusted internal FK
        reference (never a client-supplied identifier) — mirrors
        ``StrategicDecisionRepository.get_by_id`` in
        ``app/orchestration/repository.py`` exactly. Used by
        ``StrategyService.create_experiment`` to lock the exact Strategy
        row referenced by ``Hypothesis.strategy_id``."""
        query = select(Strategy).where(Strategy.id == strategy_id)
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.execute(query).scalar_one_or_none()

    def get_for_campaign_by_public_id(
        self, *, campaign_id: uuid.UUID, public_id: str, for_update: bool = False
    ) -> Strategy | None:
        """Non-leaky, campaign-scoped resource lookup — identical
        discipline to every other bounded context's own
        ``get_for_campaign_by_public_id`` (e.g.
        ``app/orchestration/repository.py::StrategicDecisionRepository``).
        Used by ``StrategyRevisionService`` to resolve the exact base
        Strategy a governed Revision targets (MVP-30B) — never a global
        lookup trusting a client-supplied campaign match."""
        query = select(Strategy).where(Strategy.campaign_id == campaign_id, Strategy.public_id == public_id)
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.execute(query).scalar_one_or_none()

    def get_current_for_campaign(self, campaign_id: uuid.UUID) -> Strategy | None:
        return self.session.execute(
            select(Strategy).where(Strategy.campaign_id == campaign_id).order_by(Strategy.version.desc()).limit(1)
        ).scalar_one_or_none()

    def list_for_campaign(self, campaign_id: uuid.UUID) -> list[Strategy]:
        return list(
            self.session.execute(
                select(Strategy).where(Strategy.campaign_id == campaign_id).order_by(Strategy.version.asc())
            )
            .scalars()
            .all()
        )

    def next_version_for_campaign(self, campaign_id: uuid.UUID) -> int:
        current = self.session.execute(
            select(func.max(Strategy.version)).where(Strategy.campaign_id == campaign_id)
        ).scalar_one()
        return (current or 0) + 1


class PositioningRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, strategy: Strategy, statement: str) -> Positioning:
        positioning = Positioning(
            public_id=generate_public_id("POS"),
            strategy_id=strategy.id,
            statement=statement,
        )
        self.session.add(positioning)
        self.session.flush()
        return positioning

    def get_for_strategy(self, strategy_id: uuid.UUID) -> Positioning | None:
        return self.session.execute(
            select(Positioning).where(Positioning.strategy_id == strategy_id)
        ).scalar_one_or_none()


class HypothesisRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, strategy: Strategy, statement: str) -> Hypothesis:
        """MVP-31B: the single-item governed counterpart to
        ``create_many`` below — used only by
        ``StrategyService.create_hypothesis`` (the new governed,
        human-reachable path). ``create_many`` remains unchanged and
        bootstrap-only (MVP-31A-R1: no ``origin`` discriminator
        distinguishes the two — a governed Hypothesis is the exact same
        domain entity as a bootstrap one, differing only in which code
        path inserted it, which is not a canonical stored property)."""
        hypothesis = Hypothesis(
            public_id=generate_public_id("HYP"),
            workspace_id=strategy.workspace_id,
            strategy_id=strategy.id,
            statement=statement,
            status=HypothesisStatus.OPEN,
        )
        self.session.add(hypothesis)
        self.session.flush()
        return hypothesis

    def create_many(self, *, strategy: Strategy, items: list[dict]) -> list[Hypothesis]:
        rows = [
            Hypothesis(
                public_id=generate_public_id("HYP"),
                workspace_id=strategy.workspace_id,
                strategy_id=strategy.id,
                statement=item["statement"],
                status=HypothesisStatus.OPEN,
            )
            for item in items
        ]
        self.session.add_all(rows)
        self.session.flush()
        return rows

    def get_by_public_id(self, public_id: str, *, for_update: bool = False) -> Hypothesis | None:
        query = select(Hypothesis).where(Hypothesis.public_id == public_id)
        if for_update:
            query = query.with_for_update()
        return self.session.execute(query).scalar_one_or_none()

    def get_by_id(self, hypothesis_id: uuid.UUID) -> Hypothesis | None:
        """MVP-37: resolves a Hypothesis from an already-trusted internal FK
        reference (``Experiment.hypothesis_id``) — read-only."""
        return self.session.execute(select(Hypothesis).where(Hypothesis.id == hypothesis_id)).scalar_one_or_none()

    def get_for_campaign_by_public_id(self, *, campaign_id: uuid.UUID, public_id: str) -> Hypothesis | None:
        """MVP-32B: non-leaky, campaign-scoped resource lookup — identical
        discipline to every other bounded context's own
        ``get_for_campaign_by_public_id`` (e.g.
        ``StrategyRepository`` above). Hypothesis carries no direct
        ``campaign_id`` column (BACKEND-01: tenant reached only through
        its own Strategy), so this joins through ``Strategy.campaign_id``
        rather than filtering a local column directly. Read-only — no
        ``for_update`` option, since Hypothesis is immutable in production
        (no reachable code path mutates one after creation; MVP-32A §N)."""
        return self.session.execute(
            select(Hypothesis)
            .join(Strategy, Hypothesis.strategy_id == Strategy.id)
            .where(Strategy.campaign_id == campaign_id, Hypothesis.public_id == public_id)
        ).scalar_one_or_none()

    def list_for_strategy(self, strategy_id: uuid.UUID) -> list[Hypothesis]:
        return list(
            self.session.execute(
                select(Hypothesis)
                .where(Hypothesis.strategy_id == strategy_id)
                .order_by(Hypothesis.created_at.asc(), Hypothesis.id.asc())
            )
            .scalars()
            .all()
        )


class ExperimentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, hypothesis: Hypothesis, description: str) -> Experiment:
        """MVP-32B: the single-item governed counterpart to
        ``create_many`` below — used only by
        ``StrategyService.create_experiment`` (the new governed,
        human-reachable path). ``create_many`` remains unchanged and
        bootstrap-only (mirrors the exact MVP-31B precedent already
        established for ``HypothesisRepository.create``/``create_many``).
        ``status`` is set to the literal string ``"RECORDED"`` — no
        native enum (MVP-32A-R1 §10-13: a single-value vocabulary does
        not justify a migration; ``experiments.status`` remains the
        existing nullable ``String(30)``)."""
        experiment = Experiment(
            public_id=generate_public_id("EXP"),
            workspace_id=hypothesis.workspace_id,
            hypothesis_id=hypothesis.id,
            description=description,
            status="RECORDED",
        )
        self.session.add(experiment)
        self.session.flush()
        return experiment

    def create_many(self, *, hypothesis: Hypothesis, items: list[dict]) -> list[Experiment]:
        rows = [
            Experiment(
                public_id=generate_public_id("EXP"),
                workspace_id=hypothesis.workspace_id,
                hypothesis_id=hypothesis.id,
                description=item["description"],
            )
            for item in items
        ]
        self.session.add_all(rows)
        self.session.flush()
        return rows

    def list_for_hypothesis(self, hypothesis_id: uuid.UUID) -> list[Experiment]:
        return list(
            self.session.execute(
                select(Experiment)
                .where(Experiment.hypothesis_id == hypothesis_id)
                .order_by(Experiment.created_at.asc(), Experiment.id.asc())
            )
            .scalars()
            .all()
        )

    def get_by_id(self, experiment_id: uuid.UUID, *, for_update: bool = False) -> Experiment | None:
        """MVP-33B: resolves an Experiment from an already-trusted internal
        FK reference (e.g. ``ContentPlan.experiment_id``) — read-only, for
        readback/public-serialization use only, mirrors
        ``StrategyRepository.get_by_id`` exactly. MVP-37: ``for_update``
        takes the Experiment row lock that serializes Definition-version
        writes (canonical order: Strategy row, then this row)."""
        query = select(Experiment).where(Experiment.id == experiment_id)
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.execute(query).scalar_one_or_none()

    def get_for_campaign_by_public_id(self, *, campaign_id: uuid.UUID, public_id: str) -> Experiment | None:
        """MVP-33B (MVP-33A-R1 §I, frozen): non-leaky, campaign-scoped
        resource lookup, extending ``HypothesisRepository.
        get_for_campaign_by_public_id``'s own exact join-through-ancestry
        technique one hop deeper — Experiment carries no ``campaign_id``
        column (only reachable via ``hypothesis_id -> Hypothesis.
        strategy_id -> Strategy.campaign_id``). Deliberately does NOT
        filter or order by Strategy currency — no ``ORDER BY version
        DESC``, no ``MAX(version)`` — so a historical-Strategy Experiment
        belonging to this Campaign remains eligible (MVP-33A §N: E2
        ALLOWED). Read-only — no ``for_update`` option, since neither
        Experiment nor its ancestry is locked or rechecked by ContentPlan
        creation (MVP-33A §N/§O: no currency recheck, no lock)."""
        return self.session.execute(
            select(Experiment)
            .join(Hypothesis, Experiment.hypothesis_id == Hypothesis.id)
            .join(Strategy, Hypothesis.strategy_id == Strategy.id)
            .where(Strategy.campaign_id == campaign_id, Experiment.public_id == public_id)
        ).scalar_one_or_none()


class ExperimentDefinitionRepository:
    """MVP-37: data access for ``ExperimentDefinitionVersion``. Append-only
    by construction — exactly one write method (``create``), no update or
    delete method exists. No method here calls ``session.commit()``."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        experiment: Experiment,
        version: int,
        client_request_id: str,
        comparison_question: str,
        comparison_type: str,
        changed_factor: str,
        controlled_factors: list[str],
        comparison_basis: str,
        scope: str,
        learning_intent: str,
        non_conclusion_boundary: str,
    ) -> ExperimentDefinitionVersion:
        row = ExperimentDefinitionVersion(
            public_id=generate_public_id("EXD"),
            workspace_id=experiment.workspace_id,
            experiment_id=experiment.id,
            version=version,
            client_request_id=client_request_id,
            comparison_question=comparison_question,
            comparison_type=comparison_type,
            changed_factor=changed_factor,
            controlled_factors=list(controlled_factors),
            comparison_basis=comparison_basis,
            scope=scope,
            learning_intent=learning_intent,
            non_conclusion_boundary=non_conclusion_boundary,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_workspace_and_request_id(
        self, *, workspace_id: uuid.UUID, client_request_id: str
    ) -> ExperimentDefinitionVersion | None:
        return self.session.execute(
            select(ExperimentDefinitionVersion)
            .where(
                ExperimentDefinitionVersion.workspace_id == workspace_id,
                ExperimentDefinitionVersion.client_request_id == client_request_id,
            )
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def get_by_public_id_for_experiment(
        self, *, experiment_id: uuid.UUID, workspace_id: uuid.UUID, public_id: str
    ) -> ExperimentDefinitionVersion | None:
        """MVP-38: resolves a version by its public ``EXD-…`` id strictly
        inside one Experiment (and workspace) — never a global lookup, so an
        id belonging to another Experiment/tenant is simply not found."""
        return self.session.execute(
            select(ExperimentDefinitionVersion).where(
                ExperimentDefinitionVersion.experiment_id == experiment_id,
                ExperimentDefinitionVersion.workspace_id == workspace_id,
                ExperimentDefinitionVersion.public_id == public_id,
            )
        ).scalar_one_or_none()

    def get_by_id(self, definition_version_id: uuid.UUID) -> ExperimentDefinitionVersion | None:
        """MVP-39: resolves a version from an already-trusted internal FK
        reference (``MeasurementContractVersion.definition_version_id``) —
        read-only, mirrors ``HypothesisRepository.get_by_id`` exactly."""
        return self.session.execute(
            select(ExperimentDefinitionVersion).where(ExperimentDefinitionVersion.id == definition_version_id)
        ).scalar_one_or_none()

    def get_tip(self, *, experiment_id: uuid.UUID, workspace_id: uuid.UUID) -> ExperimentDefinitionVersion | None:
        return self.session.execute(
            select(ExperimentDefinitionVersion)
            .where(
                ExperimentDefinitionVersion.experiment_id == experiment_id,
                ExperimentDefinitionVersion.workspace_id == workspace_id,
            )
            .order_by(ExperimentDefinitionVersion.version.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def list_for_experiment(
        self, *, experiment_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> list[ExperimentDefinitionVersion]:
        return list(
            self.session.execute(
                select(ExperimentDefinitionVersion)
                .where(
                    ExperimentDefinitionVersion.experiment_id == experiment_id,
                    ExperimentDefinitionVersion.workspace_id == workspace_id,
                )
                .order_by(ExperimentDefinitionVersion.version.asc())
            )
            .scalars()
            .all()
        )

    def tips_for_experiments(
        self, *, experiment_ids: list[uuid.UUID], workspace_id: uuid.UUID
    ) -> dict[uuid.UUID, ExperimentDefinitionVersion]:
        """The highest-version row per Experiment, for the Strategy read."""
        if not experiment_ids:
            return {}
        rows = self.session.execute(
            select(ExperimentDefinitionVersion)
            .where(
                ExperimentDefinitionVersion.experiment_id.in_(experiment_ids),
                ExperimentDefinitionVersion.workspace_id == workspace_id,
            )
            .order_by(ExperimentDefinitionVersion.version.asc())
        ).scalars()
        return {row.experiment_id: row for row in rows}


class ExperimentVariantRepository:
    """MVP-38: data access for ``ExperimentVariant``. Append-only by
    construction — exactly one write method (``create``), no update or
    delete. No method here calls ``session.commit()``."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        experiment_id: uuid.UUID,
        workspace_id: uuid.UUID,
        definition_version_id: uuid.UUID,
        ordinal: int,
        label: str,
        condition_description: str,
        client_request_id: str,
    ) -> ExperimentVariant:
        row = ExperimentVariant(
            public_id=generate_public_id("VAR"),
            workspace_id=workspace_id,
            experiment_id=experiment_id,
            definition_version_id=definition_version_id,
            ordinal=ordinal,
            label=label,
            condition_description=condition_description,
            client_request_id=client_request_id,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_workspace_and_request_id(
        self, *, workspace_id: uuid.UUID, client_request_id: str
    ) -> ExperimentVariant | None:
        return self.session.execute(
            select(ExperimentVariant)
            .where(
                ExperimentVariant.workspace_id == workspace_id,
                ExperimentVariant.client_request_id == client_request_id,
            )
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def list_labels_for_version(self, *, definition_version_id: uuid.UUID) -> list[str]:
        """Only the ``label`` column of every Variant of one version (used
        for the normalized duplicate check under the Experiment lock)."""
        return list(
            self.session.execute(
                select(ExperimentVariant.label).where(ExperimentVariant.definition_version_id == definition_version_id)
            ).scalars()
        )

    def max_ordinal_for_version(self, *, definition_version_id: uuid.UUID) -> int:
        value = self.session.execute(
            select(func.max(ExperimentVariant.ordinal)).where(
                ExperimentVariant.definition_version_id == definition_version_id
            )
        ).scalar_one_or_none()
        return int(value) if value is not None else 0

    def exists_for_definition_version(self, *, definition_version_id: uuid.UUID) -> bool:
        return (
            self.session.execute(
                select(ExperimentVariant.id)
                .where(ExperimentVariant.definition_version_id == definition_version_id)
                .limit(1)
            ).first()
            is not None
        )

    def list_by_ids(self, *, variant_ids: list[uuid.UUID]) -> list[ExperimentVariant]:
        """MVP-40: batched lookup for resolving an Authorization's
        Variant-set snapshot back to its durable label/description/public_id
        (no N+1). Order is not guaranteed — callers that need a specific
        order re-key by id."""
        if not variant_ids:
            return []
        return list(
            self.session.execute(select(ExperimentVariant).where(ExperimentVariant.id.in_(variant_ids)))
            .scalars()
            .all()
        )

    def list_for_definition_version(self, *, definition_version_id: uuid.UUID) -> list[ExperimentVariant]:
        """MVP-40: every Variant currently declared under one Definition
        version, ordered by ordinal — the complete-snapshot source for
        ``ExecutionAuthorizationService`` (frozen Design Freeze §7/§9). Read
        under the same Experiment row lock the write already holds, so the
        result is deterministic for the transaction that reads it."""
        return list(
            self.session.execute(
                select(ExperimentVariant)
                .where(ExperimentVariant.definition_version_id == definition_version_id)
                .order_by(ExperimentVariant.ordinal.asc())
            )
            .scalars()
            .all()
        )

    def counts_for_versions(
        self, *, definition_version_ids: list[uuid.UUID], workspace_id: uuid.UUID
    ) -> dict[uuid.UUID, int]:
        """One grouped query for any number of versions (no N+1)."""
        if not definition_version_ids:
            return {}
        rows = self.session.execute(
            select(ExperimentVariant.definition_version_id, func.count(ExperimentVariant.id))
            .where(
                ExperimentVariant.definition_version_id.in_(definition_version_ids),
                ExperimentVariant.workspace_id == workspace_id,
            )
            .group_by(ExperimentVariant.definition_version_id)
        ).all()
        return {version_id: count for version_id, count in rows}

    def count_for_experiment(self, *, experiment_id: uuid.UUID, workspace_id: uuid.UUID) -> int:
        return int(
            self.session.execute(
                select(func.count(ExperimentVariant.id)).where(
                    ExperimentVariant.experiment_id == experiment_id,
                    ExperimentVariant.workspace_id == workspace_id,
                )
            ).scalar_one()
        )

    def list_for_experiment(
        self, *, experiment_id: uuid.UUID, workspace_id: uuid.UUID, limit: int, offset: int
    ) -> list[tuple[ExperimentVariant, ExperimentDefinitionVersion]]:
        """Deterministic page: ordered by the pinned version's ordinal and
        then the Variant's ordinal — never by ``created_at``."""
        rows = self.session.execute(
            select(ExperimentVariant, ExperimentDefinitionVersion)
            .join(ExperimentDefinitionVersion, ExperimentVariant.definition_version_id == ExperimentDefinitionVersion.id)
            .where(
                ExperimentVariant.experiment_id == experiment_id,
                ExperimentVariant.workspace_id == workspace_id,
            )
            .order_by(ExperimentDefinitionVersion.version.asc(), ExperimentVariant.ordinal.asc())
            .limit(limit)
            .offset(offset)
        ).all()
        return [(variant, version) for variant, version in rows]


class MeasurementContractRepository:
    """MVP-39: data access for ``MeasurementContractVersion`` and its
    ``MeasurementContractRequiredSignal`` children. Append-only by
    construction — exactly one write method (``create``), which persists
    the Contract version and every RequiredSignal row as one atomic unit
    (same flush sequence, same uncommitted transaction — an IntegrityError
    on any row rolls the whole unit back together). No update or delete
    method exists. No method here calls ``session.commit()``."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        experiment_id: uuid.UUID,
        workspace_id: uuid.UUID,
        definition_version_id: uuid.UUID,
        version: int,
        measurement_window_days: int | None,
        minimum_evidence: str | None,
        success_criterion: str | None,
        analysis_method_intent: str | None,
        stopping_rule: str | None,
        decision_rule_intent: str | None,
        client_request_id: str,
        signals: list[dict],
    ) -> tuple[MeasurementContractVersion, list[MeasurementContractRequiredSignal]]:
        row = MeasurementContractVersion(
            public_id=generate_public_id("MSC"),
            workspace_id=workspace_id,
            experiment_id=experiment_id,
            definition_version_id=definition_version_id,
            version=version,
            measurement_window_days=measurement_window_days,
            minimum_evidence=minimum_evidence,
            success_criterion=success_criterion,
            analysis_method_intent=analysis_method_intent,
            stopping_rule=stopping_rule,
            decision_rule_intent=decision_rule_intent,
            client_request_id=client_request_id,
        )
        self.session.add(row)
        self.session.flush()  # assigns row.id before building the children's FK
        signal_rows = [
            MeasurementContractRequiredSignal(
                public_id=generate_public_id("RSG"),
                workspace_id=workspace_id,
                experiment_id=experiment_id,
                contract_version_id=row.id,
                ordinal=ordinal,
                name=signal["name"],
                description=signal["description"],
                expected_direction=signal.get("expected_direction"),
                evidence_requirement=signal.get("evidence_requirement"),
                tracking_required=signal.get("tracking_required", False),
            )
            for ordinal, signal in enumerate(signals, start=1)
        ]
        self.session.add_all(signal_rows)
        self.session.flush()
        return row, signal_rows

    def get_by_workspace_and_request_id(
        self, *, workspace_id: uuid.UUID, client_request_id: str
    ) -> MeasurementContractVersion | None:
        return self.session.execute(
            select(MeasurementContractVersion)
            .where(
                MeasurementContractVersion.workspace_id == workspace_id,
                MeasurementContractVersion.client_request_id == client_request_id,
            )
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def get_by_id(self, contract_version_id: uuid.UUID) -> MeasurementContractVersion | None:
        """MVP-40: resolves a version from an already-trusted internal FK
        reference (``ExecutionAuthorization.contract_version_id``) —
        read-only, mirrors ``ExperimentDefinitionRepository.get_by_id``
        exactly."""
        return self.session.execute(
            select(MeasurementContractVersion).where(MeasurementContractVersion.id == contract_version_id)
        ).scalar_one_or_none()

    def get_tip(self, *, experiment_id: uuid.UUID, workspace_id: uuid.UUID) -> MeasurementContractVersion | None:
        return self.session.execute(
            select(MeasurementContractVersion)
            .where(
                MeasurementContractVersion.experiment_id == experiment_id,
                MeasurementContractVersion.workspace_id == workspace_id,
            )
            .order_by(MeasurementContractVersion.version.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def list_for_experiment(
        self, *, experiment_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> list[MeasurementContractVersion]:
        return list(
            self.session.execute(
                select(MeasurementContractVersion)
                .where(
                    MeasurementContractVersion.experiment_id == experiment_id,
                    MeasurementContractVersion.workspace_id == workspace_id,
                )
                .order_by(MeasurementContractVersion.version.asc())
            )
            .scalars()
            .all()
        )

    def list_signals_for_version(
        self, *, contract_version_id: uuid.UUID
    ) -> list[MeasurementContractRequiredSignal]:
        return list(
            self.session.execute(
                select(MeasurementContractRequiredSignal)
                .where(MeasurementContractRequiredSignal.contract_version_id == contract_version_id)
                .order_by(MeasurementContractRequiredSignal.ordinal.asc())
            )
            .scalars()
            .all()
        )

    def get_signal_by_public_id_for_experiment(
        self, *, experiment_id: uuid.UUID, workspace_id: uuid.UUID, public_id: str
    ) -> MeasurementContractRequiredSignal | None:
        """Experiment Evidence Binding: resolves a RequiredSignal by its public
        id STRICTLY inside one Experiment and workspace — a foreign or unknown
        id is indistinguishable from a missing one. Deliberately NOT scoped to
        a Contract version: whether the signal belongs to the pinned Contract
        is a separate, typed (422) check made by the caller."""
        return self.session.execute(
            select(MeasurementContractRequiredSignal).where(
                MeasurementContractRequiredSignal.public_id == public_id,
                MeasurementContractRequiredSignal.experiment_id == experiment_id,
                MeasurementContractRequiredSignal.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

    def signal_names_for_version(self, *, contract_version_id: uuid.UUID) -> list[str]:
        """Only the ``name`` column of every RequiredSignal of one Contract
        version (used for the normalized duplicate check within one write,
        mirroring ``ExperimentVariantRepository.list_labels_for_version``)."""
        return list(
            self.session.execute(
                select(MeasurementContractRequiredSignal.name).where(
                    MeasurementContractRequiredSignal.contract_version_id == contract_version_id
                )
            ).scalars()
        )

    def exists_for_definition_version(self, *, definition_version_id: uuid.UUID) -> bool:
        return (
            self.session.execute(
                select(MeasurementContractVersion.id)
                .where(MeasurementContractVersion.definition_version_id == definition_version_id)
                .limit(1)
            ).first()
            is not None
        )

    def contract_states_for_versions(
        self, *, definition_version_ids: list[uuid.UUID], workspace_id: uuid.UUID
    ) -> dict[uuid.UUID, tuple[bool, int | None]]:
        """``{version_id: (has_measurement_contract, tip_version_or_None)}``
        — one grouped query for any number of versions (no N+1), mirroring
        ``ExperimentVariantRepository.counts_for_versions``. Because a
        Contract series' anchor ``definition_version_id`` never changes
        once it exists (the Definition is pinned by then), at most one
        version in a given series will ever carry a nonzero count here."""
        if not definition_version_ids:
            return {}
        rows = self.session.execute(
            select(MeasurementContractVersion.definition_version_id, func.max(MeasurementContractVersion.version))
            .where(
                MeasurementContractVersion.definition_version_id.in_(definition_version_ids),
                MeasurementContractVersion.workspace_id == workspace_id,
            )
            .group_by(MeasurementContractVersion.definition_version_id)
        ).all()
        tips = {version_id: tip_version for version_id, tip_version in rows}
        return {
            version_id: (version_id in tips, tips.get(version_id)) for version_id in definition_version_ids
        }


class ExecutionAuthorizationRepository:
    """MVP-40: data access for ``ExecutionAuthorization`` and its
    ``ExecutionAuthorizationVariant`` snapshot children. ``create`` persists
    the Authorization row and every snapshot child as one atomic unit (same
    flush sequence, same uncommitted transaction). Revocation/supersession
    is a direct attribute mutation on an already-loaded row performed by the
    service (mirrors ``CommercialObjectiveService.supersede_commercial_
    objective``'s own exact pattern) — no separate repository write method
    for it. No method here calls ``session.commit()``."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        experiment_id: uuid.UUID,
        workspace_id: uuid.UUID,
        definition_version_id: uuid.UUID,
        contract_version_id: uuid.UUID,
        variant_ids: list[uuid.UUID],
        unit_of_assignment: str,
        allocation_design: str,
        client_request_id: str,
    ) -> tuple[ExecutionAuthorization, list[ExecutionAuthorizationVariant]]:
        row = ExecutionAuthorization(
            public_id=generate_public_id("EXA"),
            workspace_id=workspace_id,
            experiment_id=experiment_id,
            definition_version_id=definition_version_id,
            contract_version_id=contract_version_id,
            unit_of_assignment=unit_of_assignment,
            allocation_design=allocation_design,
            client_request_id=client_request_id,
        )
        self.session.add(row)
        self.session.flush()  # assigns row.id before building the children's FK
        snapshot_rows = [
            ExecutionAuthorizationVariant(
                workspace_id=workspace_id,
                authorization_id=row.id,
                experiment_id=experiment_id,
                variant_id=variant_id,
            )
            for variant_id in variant_ids
        ]
        self.session.add_all(snapshot_rows)
        self.session.flush()
        return row, snapshot_rows

    def get_by_workspace_and_request_id(
        self, *, workspace_id: uuid.UUID, client_request_id: str
    ) -> ExecutionAuthorization | None:
        return self.session.execute(
            select(ExecutionAuthorization)
            .where(
                ExecutionAuthorization.workspace_id == workspace_id,
                ExecutionAuthorization.client_request_id == client_request_id,
            )
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def get_active_for_experiment(
        self, *, experiment_id: uuid.UUID, workspace_id: uuid.UUID, for_update: bool = False
    ) -> ExecutionAuthorization | None:
        query = select(ExecutionAuthorization).where(
            ExecutionAuthorization.experiment_id == experiment_id,
            ExecutionAuthorization.workspace_id == workspace_id,
            ExecutionAuthorization.revoked_at.is_(None),
        )
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.execute(query).scalar_one_or_none()

    def get_by_public_id_for_experiment(
        self, *, experiment_id: uuid.UUID, workspace_id: uuid.UUID, public_id: str, for_update: bool = False
    ) -> ExecutionAuthorization | None:
        """Governed Execution Start: resolves an Authorization by its public
        id STRICTLY inside one Experiment and workspace (a foreign or
        unknown id is indistinguishable from a missing one)."""
        query = select(ExecutionAuthorization).where(
            ExecutionAuthorization.public_id == public_id,
            ExecutionAuthorization.experiment_id == experiment_id,
            ExecutionAuthorization.workspace_id == workspace_id,
        )
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.execute(query).scalar_one_or_none()

    def exists_active_for_contract_version(self, *, contract_version_id: uuid.UUID) -> bool:
        """MVP-40 (frozen Design Freeze §O): the Contract-freeze check
        ``ExperimentMeasurementContractService.declare_or_revise`` performs
        under its own Experiment row lock."""
        return (
            self.session.execute(
                select(ExecutionAuthorization.id)
                .where(
                    ExecutionAuthorization.contract_version_id == contract_version_id,
                    ExecutionAuthorization.revoked_at.is_(None),
                )
                .limit(1)
            ).first()
            is not None
        )

    def list_for_experiment(
        self, *, experiment_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> list[ExecutionAuthorization]:
        """Ascending, unpaginated history (mirrors
        ``MeasurementContractRepository.list_for_experiment``'s own accepted
        limit)."""
        return list(
            self.session.execute(
                select(ExecutionAuthorization)
                .where(
                    ExecutionAuthorization.experiment_id == experiment_id,
                    ExecutionAuthorization.workspace_id == workspace_id,
                )
                .order_by(ExecutionAuthorization.created_at.asc(), ExecutionAuthorization.id.asc())
            )
            .scalars()
            .all()
        )

    def list_variants_for_authorization(
        self, *, authorization_id: uuid.UUID
    ) -> list[ExecutionAuthorizationVariant]:
        return list(
            self.session.execute(
                select(ExecutionAuthorizationVariant).where(
                    ExecutionAuthorizationVariant.authorization_id == authorization_id
                )
            )
            .scalars()
            .all()
        )


class ExecutionStartAttestationRepository:
    """Governed Execution Start: data access for ``ExecutionStartAttestation``.
    Append-only by construction — there is deliberately NO update, delete or
    correction method. No method here calls ``session.commit()``."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        workspace_id: uuid.UUID,
        authorization_id: uuid.UUID,
        started_at,
        client_request_id: str,
    ) -> ExecutionStartAttestation:
        row = ExecutionStartAttestation(
            public_id=generate_public_id("EXS"),
            workspace_id=workspace_id,
            authorization_id=authorization_id,
            started_at=started_at,
            client_request_id=client_request_id,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_workspace_and_request_id(
        self, *, workspace_id: uuid.UUID, client_request_id: str
    ) -> ExecutionStartAttestation | None:
        return self.session.execute(
            select(ExecutionStartAttestation)
            .where(
                ExecutionStartAttestation.workspace_id == workspace_id,
                ExecutionStartAttestation.client_request_id == client_request_id,
            )
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def get_for_authorization(self, *, authorization_id: uuid.UUID) -> ExecutionStartAttestation | None:
        return self.session.execute(
            select(ExecutionStartAttestation)
            .where(ExecutionStartAttestation.authorization_id == authorization_id)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def get_with_authorization_by_public_id(
        self, *, experiment_id: uuid.UUID, workspace_id: uuid.UUID, public_id: str
    ) -> tuple[ExecutionStartAttestation, ExecutionAuthorization] | None:
        """Experiment Evidence Binding: resolves a Start by its public id
        STRICTLY inside one Experiment and workspace (through its
        Authorization), together with that Authorization. A foreign or
        unknown id is indistinguishable from a missing one."""
        row = self.session.execute(
            select(ExecutionStartAttestation, ExecutionAuthorization)
            .join(
                ExecutionAuthorization,
                (ExecutionAuthorization.id == ExecutionStartAttestation.authorization_id)
                & (ExecutionAuthorization.workspace_id == ExecutionStartAttestation.workspace_id),
            )
            .where(
                ExecutionStartAttestation.public_id == public_id,
                ExecutionStartAttestation.workspace_id == workspace_id,
                ExecutionAuthorization.experiment_id == experiment_id,
            )
        ).first()
        return (row[0], row[1]) if row is not None else None

    def exists_for_authorization(self, *, authorization_id: uuid.UUID) -> bool:
        return (
            self.session.execute(
                select(ExecutionStartAttestation.id)
                .where(ExecutionStartAttestation.authorization_id == authorization_id)
                .limit(1)
            ).first()
            is not None
        )

    def exists_for_experiment(self, *, experiment_id: uuid.UUID, workspace_id: uuid.UUID) -> bool:
        """The permanent post-start freeze predicate (EXAUTH-DF-OBS-1, model
        C1): true when ANY Authorization of this Experiment has a Start,
        derived through Start -> Authorization (never a denormalized flag).
        Callers hold the Experiment row lock."""
        return (
            self.session.execute(
                select(ExecutionStartAttestation.id)
                .join(
                    ExecutionAuthorization,
                    (ExecutionAuthorization.id == ExecutionStartAttestation.authorization_id)
                    & (ExecutionAuthorization.workspace_id == ExecutionStartAttestation.workspace_id),
                )
                .where(
                    ExecutionAuthorization.experiment_id == experiment_id,
                    ExecutionAuthorization.workspace_id == workspace_id,
                )
                .limit(1)
            ).first()
            is not None
        )


class ExperimentEvidenceClaimRepository:
    """Experiment Evidence Binding: data access for ``ExperimentEvidenceClaim``.
    Append-only except for the ONE-WAY disposal (``dispose``); there is
    deliberately NO retarget, reactivate or delete method. No method here
    calls ``session.commit()``."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        workspace_id: uuid.UUID,
        start_id: uuid.UUID,
        authorization_id: uuid.UUID,
        experiment_id: uuid.UUID,
        contract_version_id: uuid.UUID,
        required_signal_id: uuid.UUID,
        metric_entry_id: uuid.UUID,
        metric_name: str,
        client_request_id: str,
        claimed_by_user_id: uuid.UUID,
    ) -> ExperimentEvidenceClaim:
        row = ExperimentEvidenceClaim(
            public_id=generate_public_id("ECL"),
            workspace_id=workspace_id,
            start_id=start_id,
            authorization_id=authorization_id,
            experiment_id=experiment_id,
            contract_version_id=contract_version_id,
            required_signal_id=required_signal_id,
            metric_entry_id=metric_entry_id,
            metric_name=metric_name,
            client_request_id=client_request_id,
            claimed_by_user_id=claimed_by_user_id,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def dispose(
        self, claim: ExperimentEvidenceClaim, *, disposed_at, disposed_by_user_id: uuid.UUID, reason: str
    ) -> None:
        """The single, one-way state change. The caller holds the claim row
        ``FOR UPDATE`` and has verified it is not yet disposed."""
        claim.disposed_at = disposed_at
        claim.disposed_by_user_id = disposed_by_user_id
        claim.disposal_reason = reason
        self.session.flush()

    def get_by_workspace_and_request_id(
        self, *, workspace_id: uuid.UUID, client_request_id: str
    ) -> ExperimentEvidenceClaim | None:
        return self.session.execute(
            select(ExperimentEvidenceClaim)
            .where(
                ExperimentEvidenceClaim.workspace_id == workspace_id,
                ExperimentEvidenceClaim.client_request_id == client_request_id,
            )
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def get_active_for_material(
        self, *, start_id: uuid.UUID, required_signal_id: uuid.UUID, metric_entry_id: uuid.UUID, metric_name: str
    ) -> ExperimentEvidenceClaim | None:
        return self.session.execute(
            select(ExperimentEvidenceClaim)
            .where(
                ExperimentEvidenceClaim.start_id == start_id,
                ExperimentEvidenceClaim.required_signal_id == required_signal_id,
                ExperimentEvidenceClaim.metric_entry_id == metric_entry_id,
                ExperimentEvidenceClaim.metric_name == metric_name,
                ExperimentEvidenceClaim.disposed_at.is_(None),
            )
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def get_for_start_by_public_id(
        self, *, start_id: uuid.UUID, workspace_id: uuid.UUID, public_id: str, for_update: bool = False
    ) -> ExperimentEvidenceClaim | None:
        """Non-leaky lookup STRICTLY inside one Start and workspace."""
        query = select(ExperimentEvidenceClaim).where(
            ExperimentEvidenceClaim.public_id == public_id,
            ExperimentEvidenceClaim.start_id == start_id,
            ExperimentEvidenceClaim.workspace_id == workspace_id,
        )
        if for_update:
            query = query.with_for_update()
        return self.session.execute(query.execution_options(populate_existing=True)).scalar_one_or_none()

    def list_for_start(self, *, start_id: uuid.UUID, workspace_id: uuid.UUID) -> list[ExperimentEvidenceClaim]:
        """Ascending, unpaginated history — active AND disposed claims alike
        (mirrors the accepted Authorization/Contract history limit,
        EEB-DF-OBS-4)."""
        return list(
            self.session.execute(
                select(ExperimentEvidenceClaim)
                .where(
                    ExperimentEvidenceClaim.start_id == start_id,
                    ExperimentEvidenceClaim.workspace_id == workspace_id,
                )
                .order_by(ExperimentEvidenceClaim.created_at.asc(), ExperimentEvidenceClaim.id.asc())
                .execution_options(populate_existing=True)
            )
            .scalars()
            .all()
        )
