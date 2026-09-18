"""MVP-30B Governed Strategy Revision (frozen MVP-30A/-30A-R1 contract):
Strategy origin integrity (nullable bootstrap fields + explicit origin
discriminator + CHECK), new strategy_revisions table, audit_events.
strategy_revision_id. Backfill: existing strategies rows -> origin=
BOOTSTRAP (deterministic, single value; every pre-existing row is
unambiguously bootstrap-origin since Revision did not exist before this
migration)."""
from alembic import op
import sqlalchemy as sa

revision = "b27209ee89a1"
down_revision = "eae9bb978d9c"
branch_labels = None
depends_on = None


def upgrade():
    # --- Strategy origin integrity -----------------------------------
    # Every prior native enum in this project was created implicitly as a
    # side effect of op.create_table(); this is the first migration that
    # adds a native-enum column to an EXISTING table via ALTER, so the
    # type must be created explicitly first.
    strategy_origin = sa.Enum("BOOTSTRAP", "REVISION", name="strategy_origin")
    strategy_origin.create(op.get_bind(), checkfirst=True)
    op.add_column("strategies", sa.Column("origin", strategy_origin, nullable=True))
    op.execute("UPDATE strategies SET origin = 'BOOTSTRAP'")
    op.alter_column("strategies", "origin", nullable=False)

    op.alter_column("strategies", "campaign_run_id", nullable=True)
    op.alter_column("strategies", "stage_execution_id", nullable=True)

    # Safe to add only after the backfill above — every existing row is
    # BOOTSTRAP with both provenance columns already NOT NULL, so all rows
    # already satisfy this CHECK at creation time.
    # Passing the bare, unprefixed name here (matching the ORM model's own
    # `name="origin_bootstrap_fields"`) — op.create_check_constraint applies
    # the project's `ck_%(table_name)s_%(constraint_name)s` naming
    # convention itself; passing the already-prefixed name would double it.
    op.create_check_constraint(
        "origin_bootstrap_fields",
        "strategies",
        "(origin = 'BOOTSTRAP' AND campaign_run_id IS NOT NULL AND stage_execution_id IS NOT NULL) OR "
        "(origin = 'REVISION' AND campaign_run_id IS NULL AND stage_execution_id IS NULL)",
    )

    # --- StrategyRevision governance-provenance table -----------------
    op.create_table(
        "strategy_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(20), nullable=False, unique=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        # Plain FK, deliberately not composite — StrategicApproval has no
        # UNIQUE(id, workspace_id) candidate key (MVP-30A-R1 §13).
        # unique=True is the actual DB-level "at most one Revision per
        # Approval" backstop (MVP-30A-R1 §H).
        sa.Column(
            "strategic_approval_id",
            sa.Uuid(),
            sa.ForeignKey("strategic_approvals.id"),
            nullable=False,
            unique=True,
        ),
        # Composite, tenant-safe FKs below — Strategy already carries the
        # (id, workspace_id) candidate key these need (MVP-30A-R1 §12).
        sa.Column("base_strategy_id", sa.Uuid(), nullable=False),
        # unique=True is the actual DB-level "at most one Revision per
        # result Strategy" backstop (MVP-30A-R1 §11).
        sa.Column("result_strategy_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_strategy_revisions_campaign_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["base_strategy_id", "workspace_id"],
            ["strategies.id", "strategies.workspace_id"],
            name="fk_strategy_revisions_base_strategy_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["result_strategy_id", "workspace_id"],
            ["strategies.id", "strategies.workspace_id"],
            name="fk_strategy_revisions_result_strategy_workspace",
        ),
    )
    for column in ("public_id", "workspace_id", "campaign_id", "strategic_approval_id", "base_strategy_id", "result_strategy_id"):
        op.create_index(
            f"ix_strategy_revisions_{column}",
            "strategy_revisions",
            [column],
            unique=(column in ("public_id", "strategic_approval_id", "result_strategy_id")),
        )

    # --- Audit integration ---------------------------------------------
    op.add_column("audit_events", sa.Column("strategy_revision_id", sa.Uuid(), nullable=True))
    op.create_index("ix_audit_events_strategy_revision_id", "audit_events", ["strategy_revision_id"])
    op.create_foreign_key(
        "fk_audit_events_strategy_revision_id_strategy_revisions",
        "audit_events",
        "strategy_revisions",
        ["strategy_revision_id"],
        ["id"],
    )


def downgrade():
    op.drop_constraint(
        "fk_audit_events_strategy_revision_id_strategy_revisions", "audit_events", type_="foreignkey"
    )
    op.drop_index("ix_audit_events_strategy_revision_id", table_name="audit_events")
    op.drop_column("audit_events", "strategy_revision_id")

    for column in ("public_id", "workspace_id", "campaign_id", "strategic_approval_id", "base_strategy_id", "result_strategy_id"):
        op.drop_index(f"ix_strategy_revisions_{column}", table_name="strategy_revisions")
    op.drop_table("strategy_revisions")

    # Bare, unprefixed name here too (see the matching comment on
    # create_check_constraint above) — op.drop_constraint with type_="check"
    # re-applies the naming convention to whatever name it's given, the
    # same way create_check_constraint does; passing the already-resolved
    # name would double the "ck_strategies_" prefix.
    op.drop_constraint("origin_bootstrap_fields", "strategies", type_="check")
    op.alter_column("strategies", "stage_execution_id", nullable=False)
    op.alter_column("strategies", "campaign_run_id", nullable=False)
    op.drop_column("strategies", "origin")

    # Without this explicit drop, a downgrade-then-upgrade round trip fails
    # with DuplicateObject: type "strategy_origin" already exists — the
    # same gotcha fixed in every prior migration in this project with a
    # native enum (see 7b4151d4cf64's own downgrade() for the identical
    # pattern).
    sa.Enum(name="strategy_origin").drop(op.get_bind(), checkfirst=True)
