"""add measurement analysis run pipeline provenance

Revision ID: 1c5dd0290557
Revises: d2af7df6e279
Create Date: 2026-09-12 17:19:18.687295

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1c5dd0290557'
down_revision: Union[str, Sequence[str], None] = 'd2af7df6e279'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    MVP-11B (MVP-11A / -R1 / -R2 / -R3 Governance): adds the Measurement
    Analysis Run pipeline's own provenance tables. Purely additive — no
    existing table's columns are modified, and none of the four frozen
    Measurement core entities (metric_entries, performance_observations,
    performance_signals, analysis_results) gains any new column. No
    backfill, no synthetic historical data: every new table starts empty.
    """
    op.create_table(
        'measurement_analysis_runs',
        sa.Column('public_id', sa.String(length=20), nullable=False),
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('campaign_id', sa.Uuid(), nullable=False),
        sa.Column('client_request_id', sa.String(length=100), nullable=False),
        sa.Column(
            'status',
            sa.Enum('RUNNING', 'COMPLETED', 'FAILED', name='measurement_analysis_run_status'),
            server_default='RUNNING',
            nullable=False,
        ),
        sa.Column('failure_reason', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ['campaign_id', 'workspace_id'], ['campaigns.id', 'campaigns.workspace_id'],
            name='fk_measurement_analysis_runs_campaign_workspace',
        ),
        sa.ForeignKeyConstraint(
            ['workspace_id'], ['workspaces.id'], name=op.f('fk_measurement_analysis_runs_workspace_id_workspaces')
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_measurement_analysis_runs')),
        sa.UniqueConstraint('id', 'workspace_id', name='uq_measurement_analysis_runs_id_workspace_id'),
        sa.UniqueConstraint(
            'workspace_id', 'client_request_id', name='uq_measurement_analysis_runs_workspace_client_request_id'
        ),
    )
    op.create_index(
        op.f('ix_measurement_analysis_runs_campaign_id'), 'measurement_analysis_runs', ['campaign_id'], unique=False
    )
    op.create_index(
        op.f('ix_measurement_analysis_runs_public_id'), 'measurement_analysis_runs', ['public_id'], unique=True
    )
    op.create_index(
        op.f('ix_measurement_analysis_runs_workspace_id'), 'measurement_analysis_runs', ['workspace_id'], unique=False
    )

    op.create_table(
        'measurement_analysis_run_metric_entries',
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('analysis_run_id', sa.Uuid(), nullable=False),
        sa.Column('metric_entry_id', sa.Uuid(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ['analysis_run_id', 'workspace_id'],
            ['measurement_analysis_runs.id', 'measurement_analysis_runs.workspace_id'],
            name='fk_measurement_analysis_run_metric_entries_run_workspace',
        ),
        sa.ForeignKeyConstraint(
            ['metric_entry_id', 'workspace_id'], ['metric_entries.id', 'metric_entries.workspace_id'],
            name='fk_measurement_analysis_run_metric_entries_entry_workspace',
        ),
        sa.ForeignKeyConstraint(
            ['workspace_id'], ['workspaces.id'],
            name=op.f('fk_measurement_analysis_run_metric_entries_workspace_id_workspaces'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_measurement_analysis_run_metric_entries')),
        sa.UniqueConstraint(
            'analysis_run_id', 'metric_entry_id', name='uq_measurement_analysis_run_metric_entries_pair'
        ),
    )
    op.create_index(
        op.f('ix_measurement_analysis_run_metric_entries_analysis_run_id'),
        'measurement_analysis_run_metric_entries', ['analysis_run_id'], unique=False,
    )
    op.create_index(
        op.f('ix_measurement_analysis_run_metric_entries_metric_entry_id'),
        'measurement_analysis_run_metric_entries', ['metric_entry_id'], unique=False,
    )
    op.create_index(
        op.f('ix_measurement_analysis_run_metric_entries_workspace_id'),
        'measurement_analysis_run_metric_entries', ['workspace_id'], unique=False,
    )

    op.create_table(
        'measurement_observation_derivations',
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('observation_id', sa.Uuid(), nullable=False),
        sa.Column('source_metric_entry_id', sa.Uuid(), nullable=False),
        sa.Column('metric_name', sa.String(length=100), nullable=False),
        sa.Column('creator_analysis_run_id', sa.Uuid(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ['observation_id', 'workspace_id'],
            ['performance_observations.id', 'performance_observations.workspace_id'],
            name='fk_measurement_observation_derivations_observation_workspace',
        ),
        sa.ForeignKeyConstraint(
            ['source_metric_entry_id', 'workspace_id'], ['metric_entries.id', 'metric_entries.workspace_id'],
            name='fk_measurement_observation_derivations_entry_workspace',
        ),
        sa.ForeignKeyConstraint(
            ['creator_analysis_run_id', 'workspace_id'],
            ['measurement_analysis_runs.id', 'measurement_analysis_runs.workspace_id'],
            name='fk_measurement_observation_derivations_run_workspace',
        ),
        sa.ForeignKeyConstraint(
            ['workspace_id'], ['workspaces.id'],
            name=op.f('fk_measurement_observation_derivations_workspace_id_workspaces'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_measurement_observation_derivations')),
        sa.UniqueConstraint('observation_id', name='uq_measurement_observation_derivations_observation_id'),
        sa.UniqueConstraint(
            'source_metric_entry_id', 'metric_name', name='uq_measurement_observation_derivations_identity'
        ),
    )
    op.create_index(
        op.f('ix_measurement_observation_derivations_creator_analysis_run_id'),
        'measurement_observation_derivations', ['creator_analysis_run_id'], unique=False,
    )
    op.create_index(
        op.f('ix_measurement_observation_derivations_observation_id'),
        'measurement_observation_derivations', ['observation_id'], unique=False,
    )
    op.create_index(
        op.f('ix_measurement_observation_derivations_source_metric_entry_id'),
        'measurement_observation_derivations', ['source_metric_entry_id'], unique=False,
    )
    op.create_index(
        op.f('ix_measurement_observation_derivations_workspace_id'),
        'measurement_observation_derivations', ['workspace_id'], unique=False,
    )

    op.create_table(
        'measurement_signal_derivations',
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('signal_id', sa.Uuid(), nullable=False),
        sa.Column('current_observation_id', sa.Uuid(), nullable=False),
        sa.Column('prior_observation_id', sa.Uuid(), nullable=False),
        sa.Column('creator_analysis_run_id', sa.Uuid(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            'current_observation_id <> prior_observation_id',
            name=op.f('ck_measurement_signal_derivations_distinct_observations'),
        ),
        sa.ForeignKeyConstraint(
            ['signal_id', 'workspace_id'], ['performance_signals.id', 'performance_signals.workspace_id'],
            name='fk_measurement_signal_derivations_signal_workspace',
        ),
        sa.ForeignKeyConstraint(
            ['current_observation_id', 'workspace_id'],
            ['performance_observations.id', 'performance_observations.workspace_id'],
            name='fk_measurement_signal_derivations_current_obs_workspace',
        ),
        sa.ForeignKeyConstraint(
            ['prior_observation_id', 'workspace_id'],
            ['performance_observations.id', 'performance_observations.workspace_id'],
            name='fk_measurement_signal_derivations_prior_obs_workspace',
        ),
        sa.ForeignKeyConstraint(
            ['creator_analysis_run_id', 'workspace_id'],
            ['measurement_analysis_runs.id', 'measurement_analysis_runs.workspace_id'],
            name='fk_measurement_signal_derivations_run_workspace',
        ),
        sa.ForeignKeyConstraint(
            ['workspace_id'], ['workspaces.id'], name=op.f('fk_measurement_signal_derivations_workspace_id_workspaces')
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_measurement_signal_derivations')),
        sa.UniqueConstraint('signal_id', name='uq_measurement_signal_derivations_signal_id'),
        sa.UniqueConstraint(
            'current_observation_id', 'prior_observation_id', name='uq_measurement_signal_derivations_identity'
        ),
    )
    op.create_index(
        op.f('ix_measurement_signal_derivations_creator_analysis_run_id'),
        'measurement_signal_derivations', ['creator_analysis_run_id'], unique=False,
    )
    op.create_index(
        op.f('ix_measurement_signal_derivations_current_observation_id'),
        'measurement_signal_derivations', ['current_observation_id'], unique=False,
    )
    op.create_index(
        op.f('ix_measurement_signal_derivations_prior_observation_id'),
        'measurement_signal_derivations', ['prior_observation_id'], unique=False,
    )
    op.create_index(
        op.f('ix_measurement_signal_derivations_signal_id'),
        'measurement_signal_derivations', ['signal_id'], unique=False,
    )
    op.create_index(
        op.f('ix_measurement_signal_derivations_workspace_id'),
        'measurement_signal_derivations', ['workspace_id'], unique=False,
    )

    op.create_table(
        'measurement_analysis_run_observation_usages',
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('analysis_run_id', sa.Uuid(), nullable=False),
        sa.Column('observation_id', sa.Uuid(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ['analysis_run_id', 'workspace_id'],
            ['measurement_analysis_runs.id', 'measurement_analysis_runs.workspace_id'],
            name='fk_measurement_analysis_run_observation_usages_run_workspace',
        ),
        sa.ForeignKeyConstraint(
            ['observation_id', 'workspace_id'],
            ['performance_observations.id', 'performance_observations.workspace_id'],
            name='fk_measurement_analysis_run_observation_usages_obs_workspace',
        ),
        sa.ForeignKeyConstraint(
            ['workspace_id'], ['workspaces.id'],
            name=op.f('fk_measurement_analysis_run_observation_usages_workspace_id_workspaces'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_measurement_analysis_run_observation_usages')),
        sa.UniqueConstraint(
            'analysis_run_id', 'observation_id', name='uq_measurement_analysis_run_observation_usages_pair'
        ),
    )
    op.create_index(
        op.f('ix_measurement_analysis_run_observation_usages_analysis_run_id'),
        'measurement_analysis_run_observation_usages', ['analysis_run_id'], unique=False,
    )
    op.create_index(
        op.f('ix_measurement_analysis_run_observation_usages_observation_id'),
        'measurement_analysis_run_observation_usages', ['observation_id'], unique=False,
    )
    op.create_index(
        op.f('ix_measurement_analysis_run_observation_usages_workspace_id'),
        'measurement_analysis_run_observation_usages', ['workspace_id'], unique=False,
    )

    op.create_table(
        'measurement_analysis_run_signal_usages',
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('analysis_run_id', sa.Uuid(), nullable=False),
        sa.Column('signal_id', sa.Uuid(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ['analysis_run_id', 'workspace_id'],
            ['measurement_analysis_runs.id', 'measurement_analysis_runs.workspace_id'],
            name='fk_measurement_analysis_run_signal_usages_run_workspace',
        ),
        sa.ForeignKeyConstraint(
            ['signal_id', 'workspace_id'], ['performance_signals.id', 'performance_signals.workspace_id'],
            name='fk_measurement_analysis_run_signal_usages_signal_workspace',
        ),
        sa.ForeignKeyConstraint(
            ['workspace_id'], ['workspaces.id'],
            name=op.f('fk_measurement_analysis_run_signal_usages_workspace_id_workspaces'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_measurement_analysis_run_signal_usages')),
        sa.UniqueConstraint('analysis_run_id', 'signal_id', name='uq_measurement_analysis_run_signal_usages_pair'),
    )
    op.create_index(
        op.f('ix_measurement_analysis_run_signal_usages_analysis_run_id'),
        'measurement_analysis_run_signal_usages', ['analysis_run_id'], unique=False,
    )
    op.create_index(
        op.f('ix_measurement_analysis_run_signal_usages_signal_id'),
        'measurement_analysis_run_signal_usages', ['signal_id'], unique=False,
    )
    op.create_index(
        op.f('ix_measurement_analysis_run_signal_usages_workspace_id'),
        'measurement_analysis_run_signal_usages', ['workspace_id'], unique=False,
    )

    op.create_table(
        'measurement_analysis_run_results',
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('analysis_run_id', sa.Uuid(), nullable=False),
        sa.Column('analysis_result_id', sa.Uuid(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ['analysis_run_id', 'workspace_id'],
            ['measurement_analysis_runs.id', 'measurement_analysis_runs.workspace_id'],
            name='fk_measurement_analysis_run_results_run_workspace',
        ),
        sa.ForeignKeyConstraint(
            ['analysis_result_id', 'workspace_id'], ['analysis_results.id', 'analysis_results.workspace_id'],
            name='fk_measurement_analysis_run_results_result_workspace',
        ),
        sa.ForeignKeyConstraint(
            ['workspace_id'], ['workspaces.id'],
            name=op.f('fk_measurement_analysis_run_results_workspace_id_workspaces'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_measurement_analysis_run_results')),
        sa.UniqueConstraint('analysis_run_id', name='uq_measurement_analysis_run_results_run_id'),
        sa.UniqueConstraint('analysis_result_id', name='uq_measurement_analysis_run_results_result_id'),
    )
    op.create_index(
        op.f('ix_measurement_analysis_run_results_analysis_result_id'),
        'measurement_analysis_run_results', ['analysis_result_id'], unique=False,
    )
    op.create_index(
        op.f('ix_measurement_analysis_run_results_analysis_run_id'),
        'measurement_analysis_run_results', ['analysis_run_id'], unique=False,
    )
    op.create_index(
        op.f('ix_measurement_analysis_run_results_workspace_id'),
        'measurement_analysis_run_results', ['workspace_id'], unique=False,
    )

    op.add_column('audit_events', sa.Column('measurement_analysis_run_id', sa.Uuid(), nullable=True))
    op.create_index(
        op.f('ix_audit_events_measurement_analysis_run_id'), 'audit_events', ['measurement_analysis_run_id'],
        unique=False,
    )
    op.create_foreign_key(
        op.f('fk_audit_events_measurement_analysis_run_id_measurement_analysis_runs'),
        'audit_events', 'measurement_analysis_runs', ['measurement_analysis_run_id'], ['id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f('fk_audit_events_measurement_analysis_run_id_measurement_analysis_runs'),
        'audit_events', type_='foreignkey',
    )
    op.drop_index(op.f('ix_audit_events_measurement_analysis_run_id'), table_name='audit_events')
    op.drop_column('audit_events', 'measurement_analysis_run_id')

    op.drop_index(
        op.f('ix_measurement_analysis_run_results_workspace_id'), table_name='measurement_analysis_run_results'
    )
    op.drop_index(
        op.f('ix_measurement_analysis_run_results_analysis_run_id'), table_name='measurement_analysis_run_results'
    )
    op.drop_index(
        op.f('ix_measurement_analysis_run_results_analysis_result_id'), table_name='measurement_analysis_run_results'
    )
    op.drop_table('measurement_analysis_run_results')

    op.drop_index(
        op.f('ix_measurement_analysis_run_signal_usages_workspace_id'),
        table_name='measurement_analysis_run_signal_usages',
    )
    op.drop_index(
        op.f('ix_measurement_analysis_run_signal_usages_signal_id'),
        table_name='measurement_analysis_run_signal_usages',
    )
    op.drop_index(
        op.f('ix_measurement_analysis_run_signal_usages_analysis_run_id'),
        table_name='measurement_analysis_run_signal_usages',
    )
    op.drop_table('measurement_analysis_run_signal_usages')

    op.drop_index(
        op.f('ix_measurement_analysis_run_observation_usages_workspace_id'),
        table_name='measurement_analysis_run_observation_usages',
    )
    op.drop_index(
        op.f('ix_measurement_analysis_run_observation_usages_observation_id'),
        table_name='measurement_analysis_run_observation_usages',
    )
    op.drop_index(
        op.f('ix_measurement_analysis_run_observation_usages_analysis_run_id'),
        table_name='measurement_analysis_run_observation_usages',
    )
    op.drop_table('measurement_analysis_run_observation_usages')

    op.drop_index(
        op.f('ix_measurement_signal_derivations_workspace_id'), table_name='measurement_signal_derivations'
    )
    op.drop_index(
        op.f('ix_measurement_signal_derivations_signal_id'), table_name='measurement_signal_derivations'
    )
    op.drop_index(
        op.f('ix_measurement_signal_derivations_prior_observation_id'), table_name='measurement_signal_derivations'
    )
    op.drop_index(
        op.f('ix_measurement_signal_derivations_current_observation_id'), table_name='measurement_signal_derivations'
    )
    op.drop_index(
        op.f('ix_measurement_signal_derivations_creator_analysis_run_id'), table_name='measurement_signal_derivations'
    )
    op.drop_table('measurement_signal_derivations')

    op.drop_index(
        op.f('ix_measurement_observation_derivations_workspace_id'), table_name='measurement_observation_derivations'
    )
    op.drop_index(
        op.f('ix_measurement_observation_derivations_source_metric_entry_id'),
        table_name='measurement_observation_derivations',
    )
    op.drop_index(
        op.f('ix_measurement_observation_derivations_observation_id'), table_name='measurement_observation_derivations'
    )
    op.drop_index(
        op.f('ix_measurement_observation_derivations_creator_analysis_run_id'),
        table_name='measurement_observation_derivations',
    )
    op.drop_table('measurement_observation_derivations')

    op.drop_index(
        op.f('ix_measurement_analysis_run_metric_entries_workspace_id'),
        table_name='measurement_analysis_run_metric_entries',
    )
    op.drop_index(
        op.f('ix_measurement_analysis_run_metric_entries_metric_entry_id'),
        table_name='measurement_analysis_run_metric_entries',
    )
    op.drop_index(
        op.f('ix_measurement_analysis_run_metric_entries_analysis_run_id'),
        table_name='measurement_analysis_run_metric_entries',
    )
    op.drop_table('measurement_analysis_run_metric_entries')

    op.drop_index(op.f('ix_measurement_analysis_runs_workspace_id'), table_name='measurement_analysis_runs')
    op.drop_index(op.f('ix_measurement_analysis_runs_public_id'), table_name='measurement_analysis_runs')
    op.drop_index(op.f('ix_measurement_analysis_runs_campaign_id'), table_name='measurement_analysis_runs')
    op.drop_table('measurement_analysis_runs')
    # `op.drop_table('measurement_analysis_runs')` above does not drop the
    # native Postgres ENUM type `measurement_analysis_run_status` that
    # column implicitly created — without this, a downgrade-then-upgrade
    # round trip fails with `DuplicateObject: type
    # "measurement_analysis_run_status" already exists` (the same gotcha
    # fixed in every prior migration in this project introducing a new
    # enum — see 654d662d78e7's own downgrade() for `metric_source`).
    sa.Enum(name="measurement_analysis_run_status").drop(op.get_bind(), checkfirst=True)
