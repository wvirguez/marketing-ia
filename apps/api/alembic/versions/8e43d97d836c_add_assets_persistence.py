"""add assets persistence

Revision ID: 8e43d97d836c
Revises: e1df89898ff2
Create Date: 2026-09-09 16:51:22.145443

BACKEND-13 Phase 2 §28: autogenerate placed ``creative_briefs`` (whose
composite FK targets ``content_pieces(id, workspace_id)``) before the new
``uq_content_pieces_id_workspace_id`` candidate key it depends on — that
ordering would fail at upgrade time (the referenced unique constraint
would not exist yet). Manually reordered so the candidate key is added
first; the downgrade removes it last, only after the dependent
``creative_briefs`` table (and therefore its FK) is already gone.

BACKEND-13 Phase 2R §7: ``assets`` deliberately carries no
``UNIQUE(id, workspace_id)`` — unlike ``content_pieces``/
``creative_briefs``, no actual FK anywhere targets that composite
(``asset_versions.asset_id`` is a plain single-column FK, and
``asset_versions``/``audit_events`` reference ``assets.id`` alone), so the
candidate key was not speculatively added.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '8e43d97d836c'
down_revision: Union[str, Sequence[str], None] = 'e1df89898ff2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. BACKEND-13 PREREQUISITE REPAIR (Phase 1D/§5): the only authorized
    # Content-domain change — a candidate key so creative_briefs can
    # declare its composite FK below. Must exist before that FK is created.
    op.create_unique_constraint('uq_content_pieces_id_workspace_id', 'content_pieces', ['id', 'workspace_id'])

    # 2. creative_briefs
    op.create_table('creative_briefs',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('content_piece_id', sa.Uuid(), nullable=False),
    sa.Column('spec', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['content_piece_id', 'workspace_id'], ['content_pieces.id', 'content_pieces.workspace_id'], name='fk_creative_briefs_content_piece_workspace'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_creative_briefs_workspace_id_workspaces')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_creative_briefs')),
    sa.UniqueConstraint('content_piece_id', name='uq_creative_briefs_content_piece_id'),
    sa.UniqueConstraint('id', 'workspace_id', name='uq_creative_briefs_id_workspace_id')
    )
    op.create_index(op.f('ix_creative_briefs_content_piece_id'), 'creative_briefs', ['content_piece_id'], unique=False)
    op.create_index(op.f('ix_creative_briefs_workspace_id'), 'creative_briefs', ['workspace_id'], unique=False)

    # 3. assets
    op.create_table('assets',
    sa.Column('public_id', sa.String(length=20), nullable=False),
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('creative_brief_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=100), nullable=False),
    sa.Column('status', sa.String(length=100), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['creative_brief_id', 'workspace_id'], ['creative_briefs.id', 'creative_briefs.workspace_id'], name='fk_assets_creative_brief_workspace'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_assets_workspace_id_workspaces')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_assets'))
    # No UNIQUE(id, workspace_id) here (Phase 2R §7) — nothing declares a
    # composite FK against it; see app/assets/models.py's Asset docstring.
    )
    op.create_index(op.f('ix_assets_creative_brief_id'), 'assets', ['creative_brief_id'], unique=False)
    op.create_index(op.f('ix_assets_public_id'), 'assets', ['public_id'], unique=True)
    op.create_index(op.f('ix_assets_workspace_id'), 'assets', ['workspace_id'], unique=False)

    # 4. asset_versions
    op.create_table('asset_versions',
    sa.Column('asset_id', sa.Uuid(), nullable=False),
    sa.Column('storage_reference', sa.String(length=2048), nullable=True),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], name=op.f('fk_asset_versions_asset_id_assets')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_asset_versions'))
    )
    op.create_index(op.f('ix_asset_versions_asset_id'), 'asset_versions', ['asset_id'], unique=False)

    # 5. audit_events nullable FK columns
    op.add_column('audit_events', sa.Column('creative_brief_id', sa.Uuid(), nullable=True))
    op.add_column('audit_events', sa.Column('asset_id', sa.Uuid(), nullable=True))
    op.add_column('audit_events', sa.Column('asset_version_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_audit_events_asset_id'), 'audit_events', ['asset_id'], unique=False)
    op.create_index(op.f('ix_audit_events_asset_version_id'), 'audit_events', ['asset_version_id'], unique=False)
    op.create_index(op.f('ix_audit_events_creative_brief_id'), 'audit_events', ['creative_brief_id'], unique=False)
    op.create_foreign_key(op.f('fk_audit_events_asset_version_id_asset_versions'), 'audit_events', 'asset_versions', ['asset_version_id'], ['id'])
    op.create_foreign_key(op.f('fk_audit_events_creative_brief_id_creative_briefs'), 'audit_events', 'creative_briefs', ['creative_brief_id'], ['id'])
    op.create_foreign_key(op.f('fk_audit_events_asset_id_assets'), 'audit_events', 'assets', ['asset_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    # 1. remove audit_events Asset FK columns/constraints
    op.drop_constraint(op.f('fk_audit_events_asset_id_assets'), 'audit_events', type_='foreignkey')
    op.drop_constraint(op.f('fk_audit_events_creative_brief_id_creative_briefs'), 'audit_events', type_='foreignkey')
    op.drop_constraint(op.f('fk_audit_events_asset_version_id_asset_versions'), 'audit_events', type_='foreignkey')
    op.drop_index(op.f('ix_audit_events_creative_brief_id'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_asset_version_id'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_asset_id'), table_name='audit_events')
    op.drop_column('audit_events', 'asset_version_id')
    op.drop_column('audit_events', 'asset_id')
    op.drop_column('audit_events', 'creative_brief_id')

    # 2. drop asset_versions
    op.drop_index(op.f('ix_asset_versions_asset_id'), table_name='asset_versions')
    op.drop_table('asset_versions')

    # 3. drop assets
    op.drop_index(op.f('ix_assets_workspace_id'), table_name='assets')
    op.drop_index(op.f('ix_assets_public_id'), table_name='assets')
    op.drop_index(op.f('ix_assets_creative_brief_id'), table_name='assets')
    op.drop_table('assets')

    # 4. drop creative_briefs (its FK depends on the content_pieces
    # candidate key removed in step 5 below — must go first)
    op.drop_index(op.f('ix_creative_briefs_workspace_id'), table_name='creative_briefs')
    op.drop_index(op.f('ix_creative_briefs_content_piece_id'), table_name='creative_briefs')
    op.drop_table('creative_briefs')

    # 5. remove the content_pieces candidate key (BACKEND-13 PREREQUISITE
    # REPAIR), only now that nothing references it anymore
    op.drop_constraint('uq_content_pieces_id_workspace_id', 'content_pieces', type_='unique')
