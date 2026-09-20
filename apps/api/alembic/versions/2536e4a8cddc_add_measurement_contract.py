"""MVP-39 Governed Measurement Contract (frozen by MVP-39A/-39B); additive only, no backfill."""
from alembic import op
import sqlalchemy as sa

revision = "2536e4a8cddc"
down_revision = "b41d7a90c2e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No new candidate key on experiment_definition_versions — this
    # migration reuses the MVP-38 candidate key
    # (uq_experiment_definition_versions_id_experiment_workspace) for the
    # composite FK below.

    op.create_table(
        "measurement_contract_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(20), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("definition_version_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("measurement_window_days", sa.Integer(), nullable=True),
        sa.Column("minimum_evidence", sa.String(1000), nullable=True),
        sa.Column("success_criterion", sa.String(1000), nullable=True),
        sa.Column("analysis_method_intent", sa.String(1000), nullable=True),
        sa.Column("stopping_rule", sa.String(1000), nullable=True),
        sa.Column("decision_rule_intent", sa.String(1000), nullable=True),
        sa.Column("client_request_id", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_measurement_contract_versions"),
        sa.UniqueConstraint("experiment_id", "version", name="uq_measurement_contract_versions_experiment_version"),
        sa.UniqueConstraint(
            "id", "experiment_id", "workspace_id", name="uq_measurement_contract_versions_id_experiment_workspace"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "client_request_id",
            name="uq_measurement_contract_versions_workspace_client_request_id",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_measurement_contract_versions_workspace_id_workspaces"
        ),
        sa.ForeignKeyConstraint(
            ["definition_version_id", "experiment_id", "workspace_id"],
            [
                "experiment_definition_versions.id",
                "experiment_definition_versions.experiment_id",
                "experiment_definition_versions.workspace_id",
            ],
            name="fk_measurement_contract_versions_definition_version_workspace",
        ),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.CheckConstraint(
            "measurement_window_days IS NULL OR measurement_window_days > 0", name="window_days_positive"
        ),
        sa.CheckConstraint(
            "(minimum_evidence IS NULL OR char_length(btrim(minimum_evidence)) > 0) AND "
            "(success_criterion IS NULL OR char_length(btrim(success_criterion)) > 0) AND "
            "(analysis_method_intent IS NULL OR char_length(btrim(analysis_method_intent)) > 0) AND "
            "(stopping_rule IS NULL OR char_length(btrim(stopping_rule)) > 0) AND "
            "(decision_rule_intent IS NULL OR char_length(btrim(decision_rule_intent)) > 0)",
            name="optional_text_nonblank",
        ),
    )
    op.create_index("ix_measurement_contract_versions_public_id", "measurement_contract_versions", ["public_id"], unique=True)
    op.create_index("ix_measurement_contract_versions_workspace_id", "measurement_contract_versions", ["workspace_id"])
    op.create_index("ix_measurement_contract_versions_experiment_id", "measurement_contract_versions", ["experiment_id"])
    op.create_index(
        "ix_measurement_contract_versions_definition_version_id",
        "measurement_contract_versions",
        ["definition_version_id"],
    )

    op.create_table(
        "measurement_contract_signals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(20), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("contract_version_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False),
        sa.Column("expected_direction", sa.String(20), nullable=True),
        sa.Column("evidence_requirement", sa.String(1000), nullable=True),
        sa.Column("tracking_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_measurement_contract_signals"),
        sa.UniqueConstraint(
            "contract_version_id", "ordinal", name="uq_measurement_contract_signals_contract_version_ordinal"
        ),
        sa.UniqueConstraint(
            "contract_version_id", "name", name="uq_measurement_contract_signals_contract_version_name"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_measurement_contract_signals_workspace_id_workspaces"
        ),
        sa.ForeignKeyConstraint(
            ["contract_version_id", "experiment_id", "workspace_id"],
            [
                "measurement_contract_versions.id",
                "measurement_contract_versions.experiment_id",
                "measurement_contract_versions.workspace_id",
            ],
            name="fk_measurement_contract_signals_contract_version_workspace",
        ),
        sa.CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        sa.CheckConstraint(
            "char_length(btrim(name)) > 0 AND char_length(btrim(description)) > 0", name="text_fields_nonblank"
        ),
        sa.CheckConstraint(
            "expected_direction IS NULL OR expected_direction IN ('INCREASE', 'DECREASE', 'TARGET', 'NO_DIRECTION')",
            name="expected_direction_valid",
        ),
        sa.CheckConstraint(
            "evidence_requirement IS NULL OR char_length(btrim(evidence_requirement)) > 0",
            name="evidence_nonblank_if_present",
        ),
    )
    op.create_index("ix_measurement_contract_signals_public_id", "measurement_contract_signals", ["public_id"], unique=True)
    op.create_index("ix_measurement_contract_signals_workspace_id", "measurement_contract_signals", ["workspace_id"])
    op.create_index("ix_measurement_contract_signals_experiment_id", "measurement_contract_signals", ["experiment_id"])
    op.create_index(
        "ix_measurement_contract_signals_contract_version_id", "measurement_contract_signals", ["contract_version_id"]
    )

    op.add_column("audit_events", sa.Column("measurement_contract_id", sa.Uuid(), nullable=True))
    op.create_index("ix_audit_events_measurement_contract_id", "audit_events", ["measurement_contract_id"])
    op.create_foreign_key(
        "fk_audit_events_measurement_contract_id",
        "audit_events",
        "measurement_contract_versions",
        ["measurement_contract_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_audit_events_measurement_contract_id", "audit_events", type_="foreignkey")
    op.drop_index("ix_audit_events_measurement_contract_id", table_name="audit_events")
    op.drop_column("audit_events", "measurement_contract_id")

    op.drop_index("ix_measurement_contract_signals_contract_version_id", table_name="measurement_contract_signals")
    op.drop_index("ix_measurement_contract_signals_experiment_id", table_name="measurement_contract_signals")
    op.drop_index("ix_measurement_contract_signals_workspace_id", table_name="measurement_contract_signals")
    op.drop_index("ix_measurement_contract_signals_public_id", table_name="measurement_contract_signals")
    op.drop_table("measurement_contract_signals")

    op.drop_index("ix_measurement_contract_versions_definition_version_id", table_name="measurement_contract_versions")
    op.drop_index("ix_measurement_contract_versions_experiment_id", table_name="measurement_contract_versions")
    op.drop_index("ix_measurement_contract_versions_workspace_id", table_name="measurement_contract_versions")
    op.drop_index("ix_measurement_contract_versions_public_id", table_name="measurement_contract_versions")
    op.drop_table("measurement_contract_versions")
