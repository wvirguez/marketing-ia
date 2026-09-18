"""MVP-33B Governed Content Plan and Experiment provenance (frozen
MVP-33A/-33A-R1 contract): ContentPlan origin integrity (nullable
run/stage-execution fields + explicit origin discriminator + CHECK,
mirroring Strategy's own MVP-30A-R1 precedent exactly), optional nullable
Experiment provenance (composite tenant-safe FK + CHECK forbidding
BOOTSTRAP rows from carrying one), and the prerequisite
experiments(id, workspace_id) candidate key. Backfill: existing
content_plans rows -> origin=BOOTSTRAP (deterministic, single value; every
pre-existing row is unambiguously bootstrap-origin since governed creation
did not exist before this migration).

Revision ID: cdaeec1bb87f
Revises: b27209ee89a1
Create Date: 2026-09-18 15:04:26.796364

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cdaeec1bb87f'
down_revision: Union[str, Sequence[str], None] = 'b27209ee89a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- prerequisite: Experiment tenant candidate key ------------------
    # Required so content_plans can declare a composite tenant-safety FK
    # on (experiment_id, workspace_id) below (MVP-33A-R1 §M/§P).
    op.create_unique_constraint(
        "uq_experiments_id_workspace_id", "experiments", ["id", "workspace_id"]
    )

    # --- ContentPlan origin integrity ------------------------------------
    # Same technique as b27209ee89a1's own strategy_origin addition: the
    # native enum type must be created explicitly before an ALTER-added
    # column can use it.
    content_plan_origin = sa.Enum("BOOTSTRAP", "GOVERNED", name="content_plan_origin")
    content_plan_origin.create(op.get_bind(), checkfirst=True)
    op.add_column("content_plans", sa.Column("origin", content_plan_origin, nullable=True))
    op.execute("UPDATE content_plans SET origin = 'BOOTSTRAP'")
    op.alter_column("content_plans", "origin", nullable=False)

    op.alter_column("content_plans", "campaign_run_id", nullable=True)
    op.alter_column("content_plans", "stage_execution_id", nullable=True)

    # Safe to add only after the backfill above — every existing row is
    # BOOTSTRAP with both provenance columns already NOT NULL, so all rows
    # already satisfy this CHECK at creation time.
    op.create_check_constraint(
        "origin_bootstrap_fields",
        "content_plans",
        "(origin = 'BOOTSTRAP' AND campaign_run_id IS NOT NULL AND stage_execution_id IS NOT NULL) OR "
        "(origin = 'GOVERNED' AND campaign_run_id IS NULL AND stage_execution_id IS NULL)",
    )

    # --- Experiment provenance (nullable, optional) -----------------------
    op.add_column("content_plans", sa.Column("experiment_id", sa.Uuid(), nullable=True))
    op.create_index("ix_content_plans_experiment_id", "content_plans", ["experiment_id"])
    op.create_foreign_key(
        "fk_content_plans_experiment_workspace",
        "content_plans",
        "experiments",
        ["experiment_id", "workspace_id"],
        ["id", "workspace_id"],
    )
    # Backfilled rows are all BOOTSTRAP with experiment_id already NULL, so
    # this CHECK is already satisfied by every existing row too.
    op.create_check_constraint(
        "bootstrap_experiment_null",
        "content_plans",
        "origin != 'BOOTSTRAP' OR experiment_id IS NULL",
    )


def downgrade() -> None:
    # Bare, unprefixed names below (matching b27209ee89a1's own identical
    # comment) — op.drop_constraint with type_="check" re-applies the
    # project's `ck_%(table_name)s_%(constraint_name)s` naming convention
    # itself; passing the already-prefixed name would double it.
    op.drop_constraint("bootstrap_experiment_null", "content_plans", type_="check")
    op.drop_constraint("fk_content_plans_experiment_workspace", "content_plans", type_="foreignkey")
    op.drop_index("ix_content_plans_experiment_id", table_name="content_plans")
    op.drop_column("content_plans", "experiment_id")

    op.drop_constraint("origin_bootstrap_fields", "content_plans", type_="check")
    op.alter_column("content_plans", "stage_execution_id", nullable=False)
    op.alter_column("content_plans", "campaign_run_id", nullable=False)
    op.drop_column("content_plans", "origin")

    # Without this explicit drop, a downgrade-then-upgrade round trip fails
    # with DuplicateObject: type "content_plan_origin" already exists —
    # the same gotcha already fixed for strategy_origin in b27209ee89a1.
    sa.Enum(name="content_plan_origin").drop(op.get_bind(), checkfirst=True)

    op.drop_constraint("uq_experiments_id_workspace_id", "experiments", type_="unique")
