"""MVP-40 Governed Execution Authorization (frozen by the Execution Authorization Discovery/Design Freeze); additive only, no backfill."""
from alembic import op
import sqlalchemy as sa

revision = "1fe7d6577113"
down_revision = "2536e4a8cddc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # New candidate key on experiment_variants: needed for
    # execution_authorization_variants' composite tenant-safe FK below.
    # Additive, no backfill — id is already unique so every existing row
    # already satisfies it.
    op.create_unique_constraint(
        "uq_experiment_variants_id_experiment_workspace",
        "experiment_variants",
        ["id", "experiment_id", "workspace_id"],
    )

    op.create_table(
        "execution_authorizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(20), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("definition_version_id", sa.Uuid(), nullable=False),
        sa.Column("contract_version_id", sa.Uuid(), nullable=False),
        sa.Column("unit_of_assignment", sa.String(200), nullable=False),
        sa.Column("allocation_design", sa.String(2000), nullable=False),
        sa.Column("client_request_id", sa.String(100), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(1000), nullable=True),
        sa.Column("superseded_by_execution_authorization_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_execution_authorizations"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_execution_authorizations_id_workspace"),
        sa.UniqueConstraint(
            "workspace_id", "client_request_id", name="uq_execution_authorizations_workspace_client_request_id"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_execution_authorizations_workspace_id_workspaces"
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id", "workspace_id"],
            ["experiments.id", "experiments.workspace_id"],
            name="fk_execution_authorizations_experiment_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["definition_version_id", "experiment_id", "workspace_id"],
            [
                "experiment_definition_versions.id",
                "experiment_definition_versions.experiment_id",
                "experiment_definition_versions.workspace_id",
            ],
            name="fk_execution_authorizations_definition_version_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["contract_version_id", "experiment_id", "workspace_id"],
            [
                "measurement_contract_versions.id",
                "measurement_contract_versions.experiment_id",
                "measurement_contract_versions.workspace_id",
            ],
            name="fk_execution_authorizations_contract_version_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_execution_authorization_id"],
            ["execution_authorizations.id"],
            name="fk_execution_authorizations_superseded_by_id",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_reason IS NOT NULL)",
            name="revocation_pairing",
        ),
        sa.CheckConstraint(
            "char_length(btrim(unit_of_assignment)) > 0 AND char_length(btrim(allocation_design)) > 0",
            name="text_fields_nonblank",
        ),
    )
    op.create_index("ix_execution_authorizations_public_id", "execution_authorizations", ["public_id"], unique=True)
    op.create_index("ix_execution_authorizations_workspace_id", "execution_authorizations", ["workspace_id"])
    op.create_index("ix_execution_authorizations_experiment_id", "execution_authorizations", ["experiment_id"])
    op.create_index(
        "ix_execution_authorizations_definition_version_id", "execution_authorizations", ["definition_version_id"]
    )
    op.create_index(
        "ix_execution_authorizations_contract_version_id", "execution_authorizations", ["contract_version_id"]
    )
    # The DB backstop for "at most one ACTIVE Authorization per Experiment"
    # (frozen Design Freeze §16) — a partial unique index, never relied on
    # as merely an application-level guard.
    op.create_index(
        "uq_execution_authorizations_experiment_active",
        "execution_authorizations",
        ["experiment_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "execution_authorization_variants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("variant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_execution_authorization_variants"),
        sa.UniqueConstraint(
            "authorization_id", "variant_id", name="uq_execution_authorization_variants_authorization_variant"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_execution_authorization_variants_workspace_id_workspaces"
        ),
        sa.ForeignKeyConstraint(
            ["authorization_id", "workspace_id"],
            ["execution_authorizations.id", "execution_authorizations.workspace_id"],
            name="fk_execution_authorization_variants_authorization_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id", "experiment_id", "workspace_id"],
            ["experiment_variants.id", "experiment_variants.experiment_id", "experiment_variants.workspace_id"],
            name="fk_execution_authorization_variants_variant_workspace",
        ),
    )
    op.create_index(
        "ix_execution_authorization_variants_workspace_id", "execution_authorization_variants", ["workspace_id"]
    )
    op.create_index(
        "ix_execution_authorization_variants_authorization_id",
        "execution_authorization_variants",
        ["authorization_id"],
    )
    op.create_index(
        "ix_execution_authorization_variants_experiment_id", "execution_authorization_variants", ["experiment_id"]
    )
    op.create_index(
        "ix_execution_authorization_variants_variant_id", "execution_authorization_variants", ["variant_id"]
    )

    op.add_column("audit_events", sa.Column("execution_authorization_id", sa.Uuid(), nullable=True))
    op.create_index("ix_audit_events_execution_authorization_id", "audit_events", ["execution_authorization_id"])
    op.create_foreign_key(
        "fk_audit_events_execution_authorization_id",
        "audit_events",
        "execution_authorizations",
        ["execution_authorization_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_audit_events_execution_authorization_id", "audit_events", type_="foreignkey")
    op.drop_index("ix_audit_events_execution_authorization_id", table_name="audit_events")
    op.drop_column("audit_events", "execution_authorization_id")

    op.drop_index("ix_execution_authorization_variants_variant_id", table_name="execution_authorization_variants")
    op.drop_index(
        "ix_execution_authorization_variants_experiment_id", table_name="execution_authorization_variants"
    )
    op.drop_index(
        "ix_execution_authorization_variants_authorization_id", table_name="execution_authorization_variants"
    )
    op.drop_index("ix_execution_authorization_variants_workspace_id", table_name="execution_authorization_variants")
    op.drop_table("execution_authorization_variants")

    op.drop_index("uq_execution_authorizations_experiment_active", table_name="execution_authorizations")
    op.drop_index("ix_execution_authorizations_contract_version_id", table_name="execution_authorizations")
    op.drop_index("ix_execution_authorizations_definition_version_id", table_name="execution_authorizations")
    op.drop_index("ix_execution_authorizations_experiment_id", table_name="execution_authorizations")
    op.drop_index("ix_execution_authorizations_workspace_id", table_name="execution_authorizations")
    op.drop_index("ix_execution_authorizations_public_id", table_name="execution_authorizations")
    op.drop_table("execution_authorizations")

    op.drop_constraint(
        "uq_experiment_variants_id_experiment_workspace", "experiment_variants", type_="unique"
    )
