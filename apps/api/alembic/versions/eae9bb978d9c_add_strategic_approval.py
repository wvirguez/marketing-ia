"""MVP-29B Strategic Approval (frozen MVP-29A contract); additive only, no
backfill."""
from alembic import op
import sqlalchemy as sa

revision = "eae9bb978d9c"
down_revision = "7b4151d4cf64"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "strategic_approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(20), nullable=False, unique=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        # Plain FK, deliberately not composite — strategic_decisions has no
        # UNIQUE(id, workspace_id) candidate key (MVP-29A §P), and adding
        # one is out of MVP-29B's authorized scope. Tenant-safety is
        # proven entirely at the service layer (see
        # app/orchestration/models.py::StrategicApproval docstring "TENANCY").
        # unique=True is the actual DB-level "at most one Approval per
        # Decision" backstop (MVP-29A §J, MVP-29B §11) — a plain, not
        # partial, UNIQUE constraint, since this row has no supersession
        # state of its own that would ever need excluding.
        sa.Column(
            "strategic_decision_id",
            sa.Uuid(),
            sa.ForeignKey("strategic_decisions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "outcome",
            sa.Enum("APPROVED", "REJECTED", name="strategic_approval_outcome"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_strategic_approvals_campaign_workspace",
        ),
    )
    for column in ("public_id", "workspace_id", "campaign_id", "strategic_decision_id"):
        op.create_index(
            f"ix_strategic_approvals_{column}",
            "strategic_approvals",
            [column],
            unique=(column in ("public_id", "strategic_decision_id")),
        )

    op.add_column("audit_events", sa.Column("strategic_approval_id", sa.Uuid(), nullable=True))
    op.create_index("ix_audit_events_strategic_approval_id", "audit_events", ["strategic_approval_id"])
    op.create_foreign_key(
        "fk_audit_events_strategic_approval_id_strategic_approvals",
        "audit_events",
        "strategic_approvals",
        ["strategic_approval_id"],
        ["id"],
    )


def downgrade():
    op.drop_constraint(
        "fk_audit_events_strategic_approval_id_strategic_approvals", "audit_events", type_="foreignkey"
    )
    op.drop_index("ix_audit_events_strategic_approval_id", table_name="audit_events")
    op.drop_column("audit_events", "strategic_approval_id")

    for column in ("public_id", "workspace_id", "campaign_id", "strategic_decision_id"):
        op.drop_index(f"ix_strategic_approvals_{column}", table_name="strategic_approvals")
    op.drop_table("strategic_approvals")

    # Without this explicit drop, a downgrade-then-upgrade round trip fails
    # with DuplicateObject: type "strategic_approval_outcome" already
    # exists — the same gotcha fixed in every prior migration in this
    # project with a native enum (see 7b4151d4cf64's own downgrade() for
    # the identical pattern).
    sa.Enum(name="strategic_approval_outcome").drop(op.get_bind(), checkfirst=True)
