"""MVP-27 Commercial Objective / Offer; additive only, no backfill."""
from alembic import op
import sqlalchemy as sa

revision = "5236a613ef1a"
down_revision = "d82530f9e7b0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "commercial_objectives",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(20), nullable=False, unique=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_commercial_objective_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_commercial_objectives_campaign_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_commercial_objective_id"],
            ["commercial_objectives.id"],
            name="fk_commercial_objectives_superseded_by_id",
        ),
        sa.CheckConstraint(
            "(superseded_at IS NULL AND superseded_by_commercial_objective_id IS NULL) OR "
            "(superseded_at IS NOT NULL AND superseded_by_commercial_objective_id IS NOT NULL)",
            name="disposition_complete",
        ),
    )
    for column in ("public_id", "workspace_id", "campaign_id"):
        op.create_index(
            f"ix_commercial_objectives_{column}", "commercial_objectives", [column], unique=(column == "public_id")
        )

    op.create_table(
        "offers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(20), nullable=False, unique=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(12, 4), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_offer_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_offers_campaign_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_offer_id"], ["offers.id"], name="fk_offers_superseded_by_offer_id_offers"
        ),
        sa.CheckConstraint(
            "(superseded_at IS NULL AND superseded_by_offer_id IS NULL) OR "
            "(superseded_at IS NOT NULL AND superseded_by_offer_id IS NOT NULL)",
            name="disposition_complete",
        ),
        sa.CheckConstraint(
            "(price IS NULL AND currency IS NULL) OR (price IS NOT NULL AND currency IS NOT NULL)",
            name="price_currency_pair",
        ),
        sa.CheckConstraint("price IS NULL OR price >= 0", name="price_non_negative"),
    )
    for column in ("public_id", "workspace_id", "campaign_id"):
        op.create_index(f"ix_offers_{column}", "offers", [column], unique=(column == "public_id"))

    op.add_column("audit_events", sa.Column("commercial_objective_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_audit_events_commercial_objective_id", "audit_events", ["commercial_objective_id"]
    )
    op.create_foreign_key(
        "fk_audit_events_commercial_objective_id_commercial_objectives",
        "audit_events",
        "commercial_objectives",
        ["commercial_objective_id"],
        ["id"],
    )

    op.add_column("audit_events", sa.Column("offer_id", sa.Uuid(), nullable=True))
    op.create_index("ix_audit_events_offer_id", "audit_events", ["offer_id"])
    op.create_foreign_key(
        "fk_audit_events_offer_id_offers", "audit_events", "offers", ["offer_id"], ["id"]
    )


def downgrade():
    op.drop_constraint("fk_audit_events_offer_id_offers", "audit_events", type_="foreignkey")
    op.drop_index("ix_audit_events_offer_id", table_name="audit_events")
    op.drop_column("audit_events", "offer_id")

    op.drop_constraint(
        "fk_audit_events_commercial_objective_id_commercial_objectives", "audit_events", type_="foreignkey"
    )
    op.drop_index("ix_audit_events_commercial_objective_id", table_name="audit_events")
    op.drop_column("audit_events", "commercial_objective_id")

    for column in ("public_id", "workspace_id", "campaign_id"):
        op.drop_index(f"ix_offers_{column}", table_name="offers")
    op.drop_table("offers")

    for column in ("public_id", "workspace_id", "campaign_id"):
        op.drop_index(f"ix_commercial_objectives_{column}", table_name="commercial_objectives")
    op.drop_table("commercial_objectives")
