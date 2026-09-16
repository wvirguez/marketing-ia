"""Add human-recorded Content Distribution lifecycle.

Revision ID: 7d0b5a4e18c2
Revises: 59405cfacd5a
"""

from alembic import op
import sqlalchemy as sa

revision = "7d0b5a4e18c2"
down_revision = "59405cfacd5a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    status = sa.Enum("READY", "DISTRIBUTED", name="content_distribution_status")
    op.create_table(
        "content_distributions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(20), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("content_piece_id", sa.Uuid(), nullable=False),
        sa.Column("content_version_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(100), nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("external_reference", sa.String(2048), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("distributed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_content_distributions"),
        sa.UniqueConstraint("content_piece_id", name="uq_content_distributions_content_piece_id"),
        sa.ForeignKeyConstraint(["content_piece_id", "workspace_id"], ["content_pieces.id", "content_pieces.workspace_id"], name="fk_content_distributions_content_piece_workspace"),
        sa.ForeignKeyConstraint(["content_version_id"], ["content_versions.id"], name="fk_content_distributions_content_version_id_content_versions"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_content_distributions_workspace_id_workspaces"),
    )
    op.create_index("ix_content_distributions_public_id", "content_distributions", ["public_id"], unique=True)
    op.create_index("ix_content_distributions_workspace_id", "content_distributions", ["workspace_id"])
    op.create_index("ix_content_distributions_content_piece_id", "content_distributions", ["content_piece_id"])
    op.create_index("ix_content_distributions_content_version_id", "content_distributions", ["content_version_id"])
    op.add_column("audit_events", sa.Column("distribution_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_audit_events_distribution_id_content_distributions", "audit_events", "content_distributions", ["distribution_id"], ["id"])
    op.create_index("ix_audit_events_distribution_id", "audit_events", ["distribution_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_distribution_id", table_name="audit_events")
    op.drop_constraint("fk_audit_events_distribution_id_content_distributions", "audit_events", type_="foreignkey")
    op.drop_column("audit_events", "distribution_id")
    op.drop_table("content_distributions")
    sa.Enum("READY", "DISTRIBUTED", name="content_distribution_status").drop(op.get_bind(), checkfirst=True)
