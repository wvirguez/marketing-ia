"""MVP-25 bounded human Learning qualification; no historical backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c6b92e815f40"
down_revision = "5d213770c589"
branch_labels = None
depends_on = None

ENUMS = {
    "learning_qualification_confidence": ("LOW", "MEDIUM", "HIGH"),
    "learning_qualification_replication_status": ("REPLICATION_NOT_ESTABLISHED", "REPLICATION_EVIDENCE_PRESENT", "REPLICATION_FAILED"),
    "learning_qualification_signal_relationship": ("SUPPORTING", "CONTRADICTING"),
    "learning_qualification_signal_removal_reason": ("ATTACHMENT_ERROR", "OTHER"),
}

def enum(name):
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def upgrade():
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(op.get_bind())
    op.create_table(
        "learning_qualifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("learning_candidate_id", sa.Uuid(), nullable=False),
        sa.Column("confidence", enum("learning_qualification_confidence")),
        sa.Column("replication_status", enum("learning_qualification_replication_status"), server_default="REPLICATION_NOT_ESTABLISHED"),
        sa.Column("scope", sa.Text()), sa.Column("generalization_boundary", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("learning_candidate_id", name="uq_lq_candidate"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_lq_id_workspace"),
        sa.ForeignKeyConstraint(["learning_candidate_id", "workspace_id"], ["learning_candidates.id", "learning_candidates.workspace_id"], name="fk_lq_candidate_workspace"),
    )
    op.create_table(
        "learning_qualification_signals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("learning_qualification_id", sa.Uuid(), nullable=False),
        sa.Column("performance_signal_id", sa.Uuid(), nullable=False),
        sa.Column("relationship", enum("learning_qualification_signal_relationship"), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.Column("removal_reason", enum("learning_qualification_signal_removal_reason")),
        sa.Column("removal_note", sa.Text()),
        sa.Column("removed_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.ForeignKeyConstraint(["learning_qualification_id", "workspace_id"], ["learning_qualifications.id", "learning_qualifications.workspace_id"], name="fk_lqs_qualification_workspace"),
        sa.ForeignKeyConstraint(["performance_signal_id", "workspace_id"], ["performance_signals.id", "performance_signals.workspace_id"], name="fk_lqs_signal_workspace"),
        sa.CheckConstraint("(removed_at IS NULL AND removal_reason IS NULL AND removal_note IS NULL AND removed_by_user_id IS NULL) OR (removed_at IS NOT NULL AND removal_reason IS NOT NULL AND removal_note IS NOT NULL AND removed_by_user_id IS NOT NULL)", name="disposition_complete"),
        sa.CheckConstraint("relationship != 'CONTRADICTING' OR (note IS NOT NULL AND length(trim(note)) > 0)", name="contradiction_note"),
        sa.CheckConstraint("removal_note IS NULL OR length(trim(removal_note)) > 0", name="removal_note_nonempty"),
    )
    for table, columns in [("learning_qualifications", ("workspace_id", "learning_candidate_id")), ("learning_qualification_signals", ("workspace_id", "learning_qualification_id", "performance_signal_id", "removed_by_user_id"))]:
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])
    for name, predicate in [("uq_lqs_active_pair", "removed_at IS NULL"), ("uq_lqs_effective_pair", "removal_reason IS NULL OR removal_reason != 'ATTACHMENT_ERROR'")]:
        op.create_index(name, "learning_qualification_signals", ["learning_qualification_id", "performance_signal_id"], unique=True, postgresql_where=sa.text(predicate))


def downgrade():
    op.drop_table("learning_qualification_signals")
    op.drop_table("learning_qualifications")
    for name in reversed(ENUMS):
        postgresql.ENUM(name=name).drop(op.get_bind())
