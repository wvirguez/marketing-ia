"""Experiment Evidence Binding (frozen by the Experiment Evidence Binding Design Freeze); additive only, no backfill.

Adds ``experiment_evidence_claims``, the four frozen candidate keys and the
claim audit FK. The migration FAILS LOUDLY — with no merge, delete, repair or
silent selection — if ``metric_values`` already holds duplicate
``(metric_entry_id, metric_name)`` pairs, because the new UNIQUE (MV1) would
otherwise be unsafe (EEB-IMPL-IC1).
"""
from alembic import op
import sqlalchemy as sa

revision = "7c1e9a4d2b68"
down_revision = "53b4bd83a005"
branch_labels = None
depends_on = None

UQ_METRIC_VALUES = "uq_metric_values_metric_entry_id_metric_name"
UQ_SIGNALS = "uq_contract_signals_id_contract_experiment_workspace"
UQ_AUTHORIZATIONS = "uq_execution_authorizations_id_contract_experiment_workspace"
UQ_STARTS = "uq_execution_start_attestations_id_authorization_workspace"


def _assert_no_duplicate_metric_values() -> None:
    """Detect existing ``(metric_entry_id, metric_name)`` duplicates BEFORE any
    DDL. Read-only: it never mutates data, so a failure leaves the database
    exactly as it was."""
    bind = op.get_bind()
    groups = bind.execute(
        sa.text(
            "select metric_entry_id, metric_name, count(*) as row_count from metric_values "
            "group by metric_entry_id, metric_name having count(*) > 1 "
            "order by metric_entry_id, metric_name"
        )
    ).fetchall()
    if groups:
        sample = ", ".join(f"({g.metric_entry_id}, {g.metric_name!r}) x{g.row_count}" for g in groups[:5])
        raise RuntimeError(
            f"Refusing to add UNIQUE(metric_entry_id, metric_name) to metric_values: {len(groups)} duplicate "
            f"group(s) already exist (first: {sample}). This migration never merges, deletes or repairs data; "
            "resolve the duplicates explicitly, then re-run."
        )


def upgrade() -> None:
    _assert_no_duplicate_metric_values()

    op.create_unique_constraint(UQ_METRIC_VALUES, "metric_values", ["metric_entry_id", "metric_name"])
    op.create_unique_constraint(
        UQ_SIGNALS,
        "measurement_contract_signals",
        ["id", "contract_version_id", "experiment_id", "workspace_id"],
    )
    op.create_unique_constraint(
        UQ_AUTHORIZATIONS,
        "execution_authorizations",
        ["id", "contract_version_id", "experiment_id", "workspace_id"],
    )
    op.create_unique_constraint(
        UQ_STARTS, "execution_start_attestations", ["id", "authorization_id", "workspace_id"]
    )

    op.create_table(
        "experiment_evidence_claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(20), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("start_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("contract_version_id", sa.Uuid(), nullable=False),
        sa.Column("required_signal_id", sa.Uuid(), nullable=False),
        sa.Column("metric_entry_id", sa.Uuid(), nullable=False),
        sa.Column("metric_name", sa.String(100), nullable=False),
        sa.Column("client_request_id", sa.String(100), nullable=False),
        sa.Column("claimed_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("disposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disposed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("disposal_reason", sa.String(1000), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_evidence_claims"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_experiment_evidence_claims_workspace_id_workspaces"
        ),
        sa.ForeignKeyConstraint(
            ["claimed_by_user_id"], ["users.id"], name="fk_experiment_evidence_claims_claimed_by_user_id_users"
        ),
        sa.ForeignKeyConstraint(
            ["disposed_by_user_id"], ["users.id"], name="fk_experiment_evidence_claims_disposed_by_user_id_users"
        ),
        # FK1: the Start (the semantic anchor) belongs to the named Authorization.
        sa.ForeignKeyConstraint(
            ["start_id", "authorization_id", "workspace_id"],
            [
                "execution_start_attestations.id",
                "execution_start_attestations.authorization_id",
                "execution_start_attestations.workspace_id",
            ],
            name="fk_experiment_evidence_claims_start_authorization",
        ),
        # FK2: the Authorization pins THIS Contract version within THIS Experiment.
        sa.ForeignKeyConstraint(
            ["authorization_id", "contract_version_id", "experiment_id", "workspace_id"],
            [
                "execution_authorizations.id",
                "execution_authorizations.contract_version_id",
                "execution_authorizations.experiment_id",
                "execution_authorizations.workspace_id",
            ],
            name="fk_experiment_evidence_claims_authorization_contract",
        ),
        # FK3: the RequiredSignal belongs to THIS Contract version within THIS Experiment.
        sa.ForeignKeyConstraint(
            ["required_signal_id", "contract_version_id", "experiment_id", "workspace_id"],
            [
                "measurement_contract_signals.id",
                "measurement_contract_signals.contract_version_id",
                "measurement_contract_signals.experiment_id",
                "measurement_contract_signals.workspace_id",
            ],
            name="fk_experiment_evidence_claims_signal_contract",
        ),
        # FK4: the MetricEntry is in the same workspace (EXISTING candidate key).
        sa.ForeignKeyConstraint(
            ["metric_entry_id", "workspace_id"],
            ["metric_entries.id", "metric_entries.workspace_id"],
            name="fk_experiment_evidence_claims_metric_entry_workspace",
        ),
        # FK5: the named metric exists in that entry.
        sa.ForeignKeyConstraint(
            ["metric_entry_id", "metric_name"],
            ["metric_values.metric_entry_id", "metric_values.metric_name"],
            name="fk_experiment_evidence_claims_metric_value",
        ),
        sa.UniqueConstraint(
            "workspace_id", "client_request_id", name="uq_experiment_evidence_claims_workspace_client_request_id"
        ),
        sa.CheckConstraint(
            "(disposed_at IS NULL AND disposed_by_user_id IS NULL AND disposal_reason IS NULL) OR "
            "(disposed_at IS NOT NULL AND disposed_by_user_id IS NOT NULL AND disposal_reason IS NOT NULL)",
            name="disposal_complete",
        ),
        sa.CheckConstraint(
            "disposal_reason IS NULL OR char_length(btrim(disposal_reason)) > 0",
            name="disposal_reason_nonblank",
        ),
    )
    op.create_index("ix_experiment_evidence_claims_public_id", "experiment_evidence_claims", ["public_id"], unique=True)
    op.create_index("ix_experiment_evidence_claims_workspace_id", "experiment_evidence_claims", ["workspace_id"])
    op.create_index("ix_experiment_evidence_claims_start_id", "experiment_evidence_claims", ["start_id"])
    op.create_index(
        "ix_experiment_evidence_claims_authorization_id", "experiment_evidence_claims", ["authorization_id"]
    )
    op.create_index("ix_experiment_evidence_claims_experiment_id", "experiment_evidence_claims", ["experiment_id"])
    op.create_index(
        "ix_experiment_evidence_claims_contract_version_id", "experiment_evidence_claims", ["contract_version_id"]
    )
    op.create_index(
        "ix_experiment_evidence_claims_required_signal_id", "experiment_evidence_claims", ["required_signal_id"]
    )
    op.create_index("ix_experiment_evidence_claims_metric_entry_id", "experiment_evidence_claims", ["metric_entry_id"])
    op.create_index(
        "uq_experiment_evidence_claims_active_datum",
        "experiment_evidence_claims",
        ["start_id", "required_signal_id", "metric_entry_id", "metric_name"],
        unique=True,
        postgresql_where=sa.text("disposed_at IS NULL"),
    )

    op.add_column("audit_events", sa.Column("experiment_evidence_claim_id", sa.Uuid(), nullable=True))
    op.create_index("ix_audit_events_experiment_evidence_claim_id", "audit_events", ["experiment_evidence_claim_id"])
    op.create_foreign_key(
        "fk_audit_events_experiment_evidence_claim_id",
        "audit_events",
        "experiment_evidence_claims",
        ["experiment_evidence_claim_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_audit_events_experiment_evidence_claim_id", "audit_events", type_="foreignkey")
    op.drop_index("ix_audit_events_experiment_evidence_claim_id", table_name="audit_events")
    op.drop_column("audit_events", "experiment_evidence_claim_id")

    op.drop_index("uq_experiment_evidence_claims_active_datum", table_name="experiment_evidence_claims")
    op.drop_index("ix_experiment_evidence_claims_metric_entry_id", table_name="experiment_evidence_claims")
    op.drop_index("ix_experiment_evidence_claims_required_signal_id", table_name="experiment_evidence_claims")
    op.drop_index("ix_experiment_evidence_claims_contract_version_id", table_name="experiment_evidence_claims")
    op.drop_index("ix_experiment_evidence_claims_experiment_id", table_name="experiment_evidence_claims")
    op.drop_index("ix_experiment_evidence_claims_authorization_id", table_name="experiment_evidence_claims")
    op.drop_index("ix_experiment_evidence_claims_start_id", table_name="experiment_evidence_claims")
    op.drop_index("ix_experiment_evidence_claims_workspace_id", table_name="experiment_evidence_claims")
    op.drop_index("ix_experiment_evidence_claims_public_id", table_name="experiment_evidence_claims")
    op.drop_table("experiment_evidence_claims")

    op.drop_constraint(UQ_STARTS, "execution_start_attestations", type_="unique")
    op.drop_constraint(UQ_AUTHORIZATIONS, "execution_authorizations", type_="unique")
    op.drop_constraint(UQ_SIGNALS, "measurement_contract_signals", type_="unique")
    op.drop_constraint(UQ_METRIC_VALUES, "metric_values", type_="unique")
