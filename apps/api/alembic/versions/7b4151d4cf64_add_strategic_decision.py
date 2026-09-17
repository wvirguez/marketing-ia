"""MVP-28B Strategic Decision (frozen MVP-28A/-R1/-R2 contract); additive
only, no backfill."""
from alembic import op
import sqlalchemy as sa

revision = "7b4151d4cf64"
down_revision = "5236a613ef1a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "strategic_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(20), nullable=False, unique=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        # Plain FK, deliberately not composite — strategic_recommendation_candidates
        # has no UNIQUE(id, workspace_id) candidate key (BACKEND-14 Governance
        # Freeze §30), and adding one is out of MVP-28B's authorized scope.
        # Tenant-safety is proven entirely at the service layer (see
        # app/orchestration/models.py::StrategicDecision docstring "TENANCY").
        sa.Column("strategic_recommendation_candidate_id", sa.Uuid(), sa.ForeignKey("strategic_recommendation_candidates.id"), nullable=True),
        sa.Column(
            "decision_type",
            sa.Enum("ADOPT", "DEFER", "DECLINE", name="strategic_decision_type"),
            nullable=False,
        ),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_strategic_decision_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_strategic_decisions_campaign_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_strategic_decision_id"],
            ["strategic_decisions.id"],
            name="fk_strategic_decisions_superseded_by_id",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "(superseded_at IS NULL AND superseded_by_strategic_decision_id IS NULL) OR "
            "(superseded_at IS NOT NULL AND superseded_by_strategic_decision_id IS NOT NULL)",
            name="disposition_complete",
        ),
    )
    for column in ("public_id", "workspace_id", "campaign_id", "strategic_recommendation_candidate_id"):
        op.create_index(
            f"ix_strategic_decisions_{column}", "strategic_decisions", [column], unique=(column == "public_id")
        )
    # The actual DB-level backstop for "at most one current Decision per
    # Recommendation" (MVP-28A-R2 §H/§14) — never relied on as merely an
    # application-level guard. A NULL strategic_recommendation_candidate_id
    # never participates in a Postgres partial-unique check.
    op.create_index(
        "uq_strategic_decisions_current_recommendation",
        "strategic_decisions",
        ["strategic_recommendation_candidate_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )

    op.add_column("audit_events", sa.Column("strategic_decision_id", sa.Uuid(), nullable=True))
    op.create_index("ix_audit_events_strategic_decision_id", "audit_events", ["strategic_decision_id"])
    op.create_foreign_key(
        "fk_audit_events_strategic_decision_id_strategic_decisions",
        "audit_events",
        "strategic_decisions",
        ["strategic_decision_id"],
        ["id"],
    )


def downgrade():
    op.drop_constraint(
        "fk_audit_events_strategic_decision_id_strategic_decisions", "audit_events", type_="foreignkey"
    )
    op.drop_index("ix_audit_events_strategic_decision_id", table_name="audit_events")
    op.drop_column("audit_events", "strategic_decision_id")

    op.drop_index("uq_strategic_decisions_current_recommendation", table_name="strategic_decisions")
    for column in ("public_id", "workspace_id", "campaign_id", "strategic_recommendation_candidate_id"):
        op.drop_index(f"ix_strategic_decisions_{column}", table_name="strategic_decisions")
    op.drop_table("strategic_decisions")

    # Without this explicit drop, a downgrade-then-upgrade round trip fails
    # with DuplicateObject: type "strategic_decision_type" already exists —
    # the same gotcha fixed in every prior migration in this project with a
    # native enum (see 448a7fa7936d's own downgrade() for the identical
    # pattern).
    sa.Enum(name="strategic_decision_type").drop(op.get_bind(), checkfirst=True)
