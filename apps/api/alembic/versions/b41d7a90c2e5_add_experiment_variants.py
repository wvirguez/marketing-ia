"""MVP-38 Governed Variant Identity (frozen by MVP-38A/-38B); additive only, no backfill."""
from alembic import op
import sqlalchemy as sa

revision = "b41d7a90c2e5"
down_revision = "7c2e91b4d0a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Candidate key so experiment_variants can reference
    # (id, experiment_id, workspace_id). `id` is already unique, so every
    # existing row satisfies it — no backfill.
    op.create_unique_constraint(
        "uq_experiment_definition_versions_id_experiment_workspace",
        "experiment_definition_versions",
        ["id", "experiment_id", "workspace_id"],
    )

    op.create_table(
        "experiment_variants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(20), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("definition_version_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("condition_description", sa.String(1000), nullable=False),
        sa.Column("client_request_id", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_variants"),
        sa.UniqueConstraint("definition_version_id", "label", name="uq_experiment_variants_definition_version_label"),
        sa.UniqueConstraint(
            "definition_version_id", "ordinal", name="uq_experiment_variants_definition_version_ordinal"
        ),
        sa.UniqueConstraint(
            "workspace_id", "client_request_id", name="uq_experiment_variants_workspace_client_request_id"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_experiment_variants_workspace_id_workspaces"
        ),
        sa.ForeignKeyConstraint(
            ["definition_version_id", "experiment_id", "workspace_id"],
            [
                "experiment_definition_versions.id",
                "experiment_definition_versions.experiment_id",
                "experiment_definition_versions.workspace_id",
            ],
            name="fk_experiment_variants_definition_version_experiment_workspace",
        ),
        sa.CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        sa.CheckConstraint(
            "char_length(btrim(label)) > 0 AND char_length(btrim(condition_description)) > 0",
            name="text_fields_nonblank",
        ),
    )
    op.create_index("ix_experiment_variants_public_id", "experiment_variants", ["public_id"], unique=True)
    op.create_index("ix_experiment_variants_workspace_id", "experiment_variants", ["workspace_id"])
    op.create_index("ix_experiment_variants_experiment_id", "experiment_variants", ["experiment_id"])
    op.create_index("ix_experiment_variants_definition_version_id", "experiment_variants", ["definition_version_id"])

    op.add_column("audit_events", sa.Column("experiment_variant_id", sa.Uuid(), nullable=True))
    op.create_index("ix_audit_events_experiment_variant_id", "audit_events", ["experiment_variant_id"])
    op.create_foreign_key(
        "fk_audit_events_experiment_variant_id_experiment_variants",
        "audit_events",
        "experiment_variants",
        ["experiment_variant_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_audit_events_experiment_variant_id_experiment_variants", "audit_events", type_="foreignkey"
    )
    op.drop_index("ix_audit_events_experiment_variant_id", table_name="audit_events")
    op.drop_column("audit_events", "experiment_variant_id")

    op.drop_index("ix_experiment_variants_definition_version_id", table_name="experiment_variants")
    op.drop_index("ix_experiment_variants_experiment_id", table_name="experiment_variants")
    op.drop_index("ix_experiment_variants_workspace_id", table_name="experiment_variants")
    op.drop_index("ix_experiment_variants_public_id", table_name="experiment_variants")
    op.drop_table("experiment_variants")

    op.drop_constraint(
        "uq_experiment_definition_versions_id_experiment_workspace",
        "experiment_definition_versions",
        type_="unique",
    )
