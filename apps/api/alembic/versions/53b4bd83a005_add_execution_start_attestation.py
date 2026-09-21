"""Governed Execution Start (frozen by the Governed Execution Start Design Freeze); additive only, no backfill."""
from alembic import op
import sqlalchemy as sa

revision = "53b4bd83a005"
down_revision = "1fe7d6577113"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_start_attestations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(20), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("client_request_id", sa.String(100), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_execution_start_attestations"),
        # At most one Start per Authorization (frozen §22) — the DB backstop.
        sa.UniqueConstraint("authorization_id", name="uq_execution_start_attestations_authorization_id"),
        sa.UniqueConstraint(
            "workspace_id", "client_request_id", name="uq_execution_start_attestations_workspace_client_request_id"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_execution_start_attestations_workspace_id_workspaces"
        ),
        # Tenant-safe composite FK to the EXISTING Authorization candidate key
        # (id, workspace_id) — no new key on any existing table.
        sa.ForeignKeyConstraint(
            ["authorization_id", "workspace_id"],
            ["execution_authorizations.id", "execution_authorizations.workspace_id"],
            name="fk_execution_start_attestations_authorization_workspace",
        ),
    )
    op.create_index("ix_execution_start_attestations_public_id", "execution_start_attestations", ["public_id"], unique=True)
    op.create_index("ix_execution_start_attestations_workspace_id", "execution_start_attestations", ["workspace_id"])
    op.create_index(
        "ix_execution_start_attestations_authorization_id", "execution_start_attestations", ["authorization_id"]
    )

    op.add_column("audit_events", sa.Column("execution_start_attestation_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_audit_events_execution_start_attestation_id", "audit_events", ["execution_start_attestation_id"]
    )
    # Explicit name: the naming-convention output would exceed PostgreSQL's
    # 63-character identifier limit.
    op.create_foreign_key(
        "fk_audit_events_execution_start_attestation_id",
        "audit_events",
        "execution_start_attestations",
        ["execution_start_attestation_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_audit_events_execution_start_attestation_id", "audit_events", type_="foreignkey")
    op.drop_index("ix_audit_events_execution_start_attestation_id", table_name="audit_events")
    op.drop_column("audit_events", "execution_start_attestation_id")

    op.drop_index("ix_execution_start_attestations_authorization_id", table_name="execution_start_attestations")
    op.drop_index("ix_execution_start_attestations_workspace_id", table_name="execution_start_attestations")
    op.drop_index("ix_execution_start_attestations_public_id", table_name="execution_start_attestations")
    op.drop_table("execution_start_attestations")
