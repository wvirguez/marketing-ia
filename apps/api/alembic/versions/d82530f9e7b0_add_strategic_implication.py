"""MVP-26 Strategic Implication; additive only, no backfill."""
from alembic import op
import sqlalchemy as sa

revision = "d82530f9e7b0"
down_revision = "c6b92e815f40"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "strategic_implications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(20), nullable=False, unique=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("learning_candidate_id", sa.Uuid(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("id", "workspace_id", name="uq_strategic_implications_id_workspace_id"),
        sa.ForeignKeyConstraint(
            ["learning_candidate_id", "workspace_id"],
            ["learning_candidates.id", "learning_candidates.workspace_id"],
            name="fk_strategic_implications_learning_candidate_ws",
        ),
    )
    for column in ("public_id", "workspace_id", "learning_candidate_id"):
        op.create_index(f"ix_strategic_implications_{column}", "strategic_implications", [column], unique=(column == "public_id"))

    # Nullable for legacy compatibility only — existing
    # strategic_recommendation_candidates rows predate this linkage and
    # must never be backfilled with an invented implication. New-write
    # governance (every new Recommendation must reference exactly one
    # StrategicImplication) is enforced in app/learning/service.py and
    # app/learning/schemas.py, never as a database NOT NULL constraint.
    op.add_column(
        "strategic_recommendation_candidates",
        sa.Column("strategic_implication_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_strategic_recommendation_candidates_strategic_implication_id",
        "strategic_recommendation_candidates",
        ["strategic_implication_id"],
    )
    op.create_foreign_key(
        "fk_strategic_recommendation_candidates_strategic_implication_ws",
        "strategic_recommendation_candidates",
        "strategic_implications",
        ["strategic_implication_id", "workspace_id"],
        ["id", "workspace_id"],
    )

    op.add_column(
        "audit_events",
        sa.Column("strategic_implication_id", sa.Uuid(), sa.ForeignKey("strategic_implications.id"), nullable=True),
    )
    op.create_index("ix_audit_events_strategic_implication_id", "audit_events", ["strategic_implication_id"])


def downgrade():
    op.drop_index("ix_audit_events_strategic_implication_id", table_name="audit_events")
    op.drop_column("audit_events", "strategic_implication_id")

    op.drop_constraint(
        "fk_strategic_recommendation_candidates_strategic_implication_ws",
        "strategic_recommendation_candidates",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_strategic_recommendation_candidates_strategic_implication_id",
        table_name="strategic_recommendation_candidates",
    )
    op.drop_column("strategic_recommendation_candidates", "strategic_implication_id")

    for column in ("public_id", "workspace_id", "learning_candidate_id"):
        op.drop_index(f"ix_strategic_implications_{column}", table_name="strategic_implications")
    op.drop_table("strategic_implications")
