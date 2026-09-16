"""Add ContentDistribution <-> TrackingRequirement association.

Revision ID: 5d213770c589
Revises: a1f3c9e07d5b
"""

from alembic import op
import sqlalchemy as sa

revision = "5d213770c589"
down_revision = "a1f3c9e07d5b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MVP-24 (frozen by MVP-24A/MVP-24A-R1): IDENTITY-ONLY HISTORICAL
    # ASSOCIATION between one ContentDistribution and one
    # TrackingRequirement. No snapshot columns, no version columns, no
    # history table — pair identity + created_at only (MVP-24A-R1 §P).
    # No AuditEvent schema change: audit_events.distribution_id and
    # audit_events.tracking_requirement_id already exist (added by
    # BACKEND-11/BACKEND-15) and are reused as-is (MVP-24A-R1 §Q).
    op.create_table(
        "content_distribution_tracking_requirements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("content_distribution_id", sa.Uuid(), nullable=False),
        sa.Column("tracking_requirement_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_content_distribution_tracking_requirements"),
        sa.UniqueConstraint(
            "content_distribution_id", "tracking_requirement_id",
            name="uq_content_distribution_tracking_requirements_pair",
        ),
        sa.ForeignKeyConstraint(
            ["content_distribution_id", "workspace_id"],
            ["content_distributions.id", "content_distributions.workspace_id"],
            name="fk_content_distribution_tracking_requirements_distribution_ws",
        ),
        sa.ForeignKeyConstraint(
            ["tracking_requirement_id"], ["tracking_requirements.id"],
            name="fk_content_distribution_tracking_requirements_requirement",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"],
            name="fk_content_distribution_tracking_requirements_workspace_id",
        ),
    )
    op.create_index(
        "ix_content_distribution_tracking_requirements_workspace_id",
        "content_distribution_tracking_requirements", ["workspace_id"],
    )
    op.create_index(
        "ix_content_distribution_tracking_requirements_distribution_id",
        "content_distribution_tracking_requirements", ["content_distribution_id"],
    )
    op.create_index(
        "ix_content_distribution_tracking_requirements_requirement_id",
        "content_distribution_tracking_requirements", ["tracking_requirement_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_content_distribution_tracking_requirements_requirement_id",
        table_name="content_distribution_tracking_requirements",
    )
    op.drop_index(
        "ix_content_distribution_tracking_requirements_distribution_id",
        table_name="content_distribution_tracking_requirements",
    )
    op.drop_index(
        "ix_content_distribution_tracking_requirements_workspace_id",
        table_name="content_distribution_tracking_requirements",
    )
    op.drop_table("content_distribution_tracking_requirements")
