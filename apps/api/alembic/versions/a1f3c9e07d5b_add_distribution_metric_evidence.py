"""Add Distribution-linked Measurement Evidence.

Revision ID: a1f3c9e07d5b
Revises: 7d0b5a4e18c2
"""

from alembic import op
import sqlalchemy as sa

revision = "a1f3c9e07d5b"
down_revision = "7d0b5a4e18c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MVP-19B: additive-only candidate key so distribution_metric_evidence
    # can declare a composite, tenant-safe FK on (distribution_id,
    # workspace_id) — no cardinality change to content_distributions.
    op.create_unique_constraint(
        "uq_content_distributions_id_workspace_id", "content_distributions", ["id", "workspace_id"]
    )

    op.create_table(
        "distribution_metric_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(20), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("distribution_id", sa.Uuid(), nullable=False),
        sa.Column("metric_entry_id", sa.Uuid(), nullable=False),
        sa.Column("supersedes_evidence_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("source_reference", sa.String(2048), nullable=True),
        sa.Column("correction_reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_distribution_metric_evidence"),
        sa.UniqueConstraint("metric_entry_id", name="uq_distribution_metric_evidence_metric_entry_id"),
        sa.UniqueConstraint("supersedes_evidence_id", name="uq_distribution_metric_evidence_supersedes_evidence_id"),
        sa.UniqueConstraint(
            "id", "distribution_id", "workspace_id",
            name="uq_distribution_metric_evidence_id_distribution_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["distribution_id", "workspace_id"],
            ["content_distributions.id", "content_distributions.workspace_id"],
            name="fk_distribution_metric_evidence_distribution_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["metric_entry_id", "workspace_id"],
            ["metric_entries.id", "metric_entries.workspace_id"],
            name="fk_distribution_metric_evidence_metric_entry_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_evidence_id", "distribution_id", "workspace_id"],
            [
                "distribution_metric_evidence.id",
                "distribution_metric_evidence.distribution_id",
                "distribution_metric_evidence.workspace_id",
            ],
            name="fk_distribution_metric_evidence_supersedes_same_distribution",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_distribution_metric_evidence_workspace_id_workspaces"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name="fk_distribution_metric_evidence_created_by_user_id_users"),
        sa.CheckConstraint(
            "(supersedes_evidence_id IS NULL AND correction_reason IS NULL) "
            "OR (supersedes_evidence_id IS NOT NULL AND correction_reason IS NOT NULL)",
            name="ck_distribution_metric_evidence_correction_reason_pairing",
        ),
    )
    op.create_index("ix_distribution_metric_evidence_public_id", "distribution_metric_evidence", ["public_id"], unique=True)
    op.create_index("ix_distribution_metric_evidence_workspace_id", "distribution_metric_evidence", ["workspace_id"])
    op.create_index("ix_distribution_metric_evidence_distribution_id", "distribution_metric_evidence", ["distribution_id"])
    op.create_index("ix_distribution_metric_evidence_metric_entry_id", "distribution_metric_evidence", ["metric_entry_id"])
    op.create_index("ix_distribution_metric_evidence_created_at", "distribution_metric_evidence", ["created_at"])
    op.create_index(
        "ix_distribution_metric_evidence_distribution_created_id",
        "distribution_metric_evidence", ["distribution_id", "created_at", "id"],
    )

    op.add_column("audit_events", sa.Column("distribution_metric_evidence_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_audit_events_distribution_metric_evidence_id",
        "audit_events", "distribution_metric_evidence", ["distribution_metric_evidence_id"], ["id"],
    )
    op.create_index(
        "ix_audit_events_distribution_metric_evidence_id", "audit_events", ["distribution_metric_evidence_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_distribution_metric_evidence_id", table_name="audit_events")
    op.drop_constraint(
        "fk_audit_events_distribution_metric_evidence_id", "audit_events", type_="foreignkey"
    )
    op.drop_column("audit_events", "distribution_metric_evidence_id")

    op.drop_index("ix_distribution_metric_evidence_distribution_created_id", table_name="distribution_metric_evidence")
    op.drop_index("ix_distribution_metric_evidence_created_at", table_name="distribution_metric_evidence")
    op.drop_index("ix_distribution_metric_evidence_metric_entry_id", table_name="distribution_metric_evidence")
    op.drop_index("ix_distribution_metric_evidence_distribution_id", table_name="distribution_metric_evidence")
    op.drop_index("ix_distribution_metric_evidence_workspace_id", table_name="distribution_metric_evidence")
    op.drop_index("ix_distribution_metric_evidence_public_id", table_name="distribution_metric_evidence")
    op.drop_table("distribution_metric_evidence")

    op.drop_constraint("uq_content_distributions_id_workspace_id", "content_distributions", type_="unique")
