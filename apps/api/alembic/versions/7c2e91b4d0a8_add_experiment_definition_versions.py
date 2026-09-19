"""MVP-37 Governed Experiment Definition (frozen by MVP-37A/-37B); additive only, no backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "7c2e91b4d0a8"
down_revision = "41687fa37c1c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiment_definition_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(20), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("comparison_question", sa.String(1000), nullable=False),
        sa.Column("comparison_type", sa.String(20), nullable=False),
        sa.Column("changed_factor", sa.String(200), nullable=False),
        sa.Column("controlled_factors", postgresql.JSONB(), nullable=False),
        sa.Column("comparison_basis", sa.String(1000), nullable=False),
        sa.Column("scope", sa.String(1000), nullable=False),
        sa.Column("learning_intent", sa.String(1000), nullable=False),
        sa.Column("non_conclusion_boundary", sa.String(1000), nullable=False),
        sa.Column("client_request_id", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_definition_versions"),
        sa.UniqueConstraint(
            "experiment_id", "version", name="uq_experiment_definition_versions_experiment_version"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "client_request_id",
            name="uq_experiment_definition_versions_workspace_client_request_id",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_experiment_definition_versions_workspace_id_workspaces"
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id", "workspace_id"],
            ["experiments.id", "experiments.workspace_id"],
            name="fk_experiment_definition_versions_experiment_workspace",
        ),
        sa.CheckConstraint("comparison_type IN ('OBSERVATIONAL', 'CONTROLLED')", name="comparison_type_valid"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.CheckConstraint("jsonb_typeof(controlled_factors) = 'array'", name="controlled_factors_is_array"),
        sa.CheckConstraint(
            "CASE WHEN jsonb_typeof(controlled_factors) = 'array' "
            "THEN jsonb_array_length(controlled_factors) <= 20 ELSE false END",
            name="controlled_factors_max_count",
        ),
        sa.CheckConstraint(
            "comparison_type <> 'CONTROLLED' OR (CASE WHEN jsonb_typeof(controlled_factors) = 'array' "
            "THEN jsonb_array_length(controlled_factors) >= 1 ELSE false END)",
            name="controlled_needs_factors",
        ),
        sa.CheckConstraint(
            "char_length(btrim(comparison_question)) > 0 AND char_length(btrim(changed_factor)) > 0 "
            "AND char_length(btrim(comparison_basis)) > 0 AND char_length(btrim(scope)) > 0 "
            "AND char_length(btrim(learning_intent)) > 0 AND char_length(btrim(non_conclusion_boundary)) > 0",
            name="text_fields_nonblank",
        ),
    )
    op.create_index(
        "ix_experiment_definition_versions_public_id", "experiment_definition_versions", ["public_id"], unique=True
    )
    op.create_index(
        "ix_experiment_definition_versions_workspace_id", "experiment_definition_versions", ["workspace_id"]
    )
    op.create_index(
        "ix_experiment_definition_versions_experiment_id", "experiment_definition_versions", ["experiment_id"]
    )

    op.add_column("audit_events", sa.Column("experiment_definition_version_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_audit_events_experiment_definition_version_id", "audit_events", ["experiment_definition_version_id"]
    )
    op.create_foreign_key(
        "fk_audit_events_experiment_definition_version_id",
        "audit_events",
        "experiment_definition_versions",
        ["experiment_definition_version_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_audit_events_experiment_definition_version_id", "audit_events", type_="foreignkey")
    op.drop_index("ix_audit_events_experiment_definition_version_id", table_name="audit_events")
    op.drop_column("audit_events", "experiment_definition_version_id")

    op.drop_index("ix_experiment_definition_versions_experiment_id", table_name="experiment_definition_versions")
    op.drop_index("ix_experiment_definition_versions_workspace_id", table_name="experiment_definition_versions")
    op.drop_index("ix_experiment_definition_versions_public_id", table_name="experiment_definition_versions")
    op.drop_table("experiment_definition_versions")
