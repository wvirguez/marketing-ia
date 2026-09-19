"""MVP-36 Governed CommercialOutcome (frozen by MVP-36A/-R1); additive only, no backfill."""
from alembic import op
import sqlalchemy as sa

revision = "41687fa37c1c"
down_revision = "cdaeec1bb87f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "commercial_outcomes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(20), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("content_distribution_id", sa.Uuid(), nullable=True),
        sa.Column("outcome_type", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("monetary_value", sa.Numeric(12, 4), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("external_reference", sa.String(2048), nullable=True),
        sa.Column("client_request_id", sa.String(100), nullable=False),
        sa.Column("supersedes_outcome_id", sa.Uuid(), nullable=True),
        sa.Column("correction_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_commercial_outcomes"),
        sa.UniqueConstraint(
            "id", "campaign_id", "workspace_id", name="uq_commercial_outcomes_id_campaign_workspace"
        ),
        sa.UniqueConstraint(
            "workspace_id", "client_request_id", name="uq_commercial_outcomes_workspace_client_request_id"
        ),
        sa.UniqueConstraint("supersedes_outcome_id", name="uq_commercial_outcomes_supersedes_outcome_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_commercial_outcomes_workspace_id_workspaces"
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_commercial_outcomes_campaign_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["content_distribution_id", "workspace_id"],
            ["content_distributions.id", "content_distributions.workspace_id"],
            name="fk_commercial_outcomes_distribution_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_outcome_id", "campaign_id", "workspace_id"],
            [
                "commercial_outcomes.id",
                "commercial_outcomes.campaign_id",
                "commercial_outcomes.workspace_id",
            ],
            name="fk_commercial_outcomes_supersedes_same_campaign",
        ),
        sa.CheckConstraint("quantity IS NULL OR quantity > 0", name="quantity_positive"),
        sa.CheckConstraint(
            "(monetary_value IS NULL AND currency IS NULL) OR (monetary_value IS NOT NULL AND currency IS NOT NULL)",
            name="monetary_value_currency_pair",
        ),
        sa.CheckConstraint("monetary_value IS NULL OR monetary_value >= 0", name="monetary_value_non_negative"),
        sa.CheckConstraint(
            "(supersedes_outcome_id IS NULL AND correction_reason IS NULL) "
            "OR (supersedes_outcome_id IS NOT NULL AND correction_reason IS NOT NULL)",
            name="correction_reason_pairing",
        ),
    )
    op.create_index("ix_commercial_outcomes_public_id", "commercial_outcomes", ["public_id"], unique=True)
    op.create_index("ix_commercial_outcomes_workspace_id", "commercial_outcomes", ["workspace_id"])
    op.create_index("ix_commercial_outcomes_campaign_id", "commercial_outcomes", ["campaign_id"])
    op.create_index(
        "ix_commercial_outcomes_content_distribution_id", "commercial_outcomes", ["content_distribution_id"]
    )

    op.add_column("audit_events", sa.Column("commercial_outcome_id", sa.Uuid(), nullable=True))
    op.create_index("ix_audit_events_commercial_outcome_id", "audit_events", ["commercial_outcome_id"])
    op.create_foreign_key(
        "fk_audit_events_commercial_outcome_id_commercial_outcomes",
        "audit_events",
        "commercial_outcomes",
        ["commercial_outcome_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_audit_events_commercial_outcome_id_commercial_outcomes", "audit_events", type_="foreignkey"
    )
    op.drop_index("ix_audit_events_commercial_outcome_id", table_name="audit_events")
    op.drop_column("audit_events", "commercial_outcome_id")

    op.drop_index("ix_commercial_outcomes_content_distribution_id", table_name="commercial_outcomes")
    op.drop_index("ix_commercial_outcomes_campaign_id", table_name="commercial_outcomes")
    op.drop_index("ix_commercial_outcomes_workspace_id", table_name="commercial_outcomes")
    op.drop_index("ix_commercial_outcomes_public_id", table_name="commercial_outcomes")
    op.drop_table("commercial_outcomes")
