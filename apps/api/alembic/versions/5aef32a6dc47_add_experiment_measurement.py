"""Experiment Measurement (frozen Discovery/Design Freeze/Pre-Implementation
Reconciliation/Final Relational Integrity Reconciliation): one new governed
aggregate root (``ExperimentMeasurementRun``, public prefix ``EXM``) plus its
three pure children — ``experiment_measurement_signal_outputs``,
``experiment_measurement_slice_outputs``, ``experiment_measurement_datum_usages``
— under ``app/strategy``, distinct from the unrelated
``app.measurement.MeasurementAnalysisRun`` pipeline.

Also purely additive:

* one candidate key on ``experiment_measurement_signal_outputs`` itself,
  ``(id, workspace_id)``, purely so ``experiment_measurement_slice_outputs``
  can declare a composite tenant-safe FK — the same "candidate key purely so
  a child can declare a composite FK" pattern used throughout this domain
  (verified empirically against real PostgreSQL: a composite FK cannot
  target a table's bare primary key, only an explicit UNIQUE/PK constraint
  covering exactly its own referenced column set).
* two candidate keys on the already-shipped ``experiment_evidence_claims``
  table — ``(id, start_id, workspace_id)`` and
  ``(id, required_signal_id, workspace_id)`` — needed so
  ``experiment_measurement_datum_usages`` can prove, at the database level,
  that the Claim it references shares its Run's exact Start (Reconciliation
  RI-2) and is labelled under the Claim's own true RequiredSignal
  (Reconciliation §7). ``id`` is already unique, so both are trivially
  satisfied by every existing row — no data changes, no backfill.
* one nullable ``experiment_measurement_run_id`` FK column on the already-
  shipped ``audit_events`` table, matching the identical pattern already used
  for every other governed capability's own creation event.

Every constraint name below is verified EXACTLY against ``Base.metadata``'s
own naming convention (``ck_%(table_name)s_%(constraint_name)s`` for CHECKs
— every explicitly-named CheckConstraint is prefixed with its table name a
second time; ``fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s``
for any FK left unnamed at the ORM level, auto-hash-shortened by SQLAlchemy
past 63 characters in a way this hand-written migration cannot reliably
reproduce) — not hand-derived. Several tables' own names are long enough
that PostgreSQL's 63-character identifier limit forced every FK and several
CHECK constraints onto explicit, shortened names at the ORM level
(``app/strategy/models.py``); this migration reproduces those exact names,
confirmed empirically via a real-PostgreSQL ``create_all()`` round trip.

DOWNGRADE HAZARD (accepted, same class as PEMD's own): downgrading drops the
four new tables, so any Measurement history created after this revision is
LOST. The downgrade is schema-reversible, not semantically lossless.
"""
from alembic import op
import sqlalchemy as sa

revision = "5aef32a6dc47"
down_revision = "9d4b7e2a51c3"
branch_labels = None
depends_on = None

RUN_TABLE = "experiment_measurement_runs"
SIGNAL_OUTPUT_TABLE = "experiment_measurement_signal_outputs"
SLICE_OUTPUT_TABLE = "experiment_measurement_slice_outputs"
DATUM_USAGE_TABLE = "experiment_measurement_datum_usages"
CLAIM_TABLE = "experiment_evidence_claims"


def upgrade() -> None:
    # Additive candidate keys on the already-shipped ExperimentEvidenceClaim
    # (Reconciliation RI-2/§7) — zero data change, "id" is already unique.
    # Created FIRST: experiment_measurement_datum_usages' own FKs below
    # target these two constraints, and PostgreSQL requires the referenced
    # unique constraint to exist before the referencing FK is declared.
    op.create_unique_constraint(
        "uq_experiment_evidence_claims_id_start_workspace", CLAIM_TABLE, ["id", "start_id", "workspace_id"]
    )
    op.create_unique_constraint(
        "uq_experiment_evidence_claims_id_required_signal_workspace",
        CLAIM_TABLE,
        ["id", "required_signal_id", "workspace_id"],
    )

    op.create_table(
        RUN_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(20), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_id", sa.Uuid(), nullable=False),
        sa.Column("start_id", sa.Uuid(), nullable=False),
        sa.Column("contract_version_id", sa.Uuid(), nullable=False),
        sa.Column("declaration_semantics_version", sa.Integer(), nullable=False),
        sa.Column("client_request_id", sa.String(100), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_measurement_runs"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_measurement_runs_workspace_id"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name="fk_measurement_runs_created_by_user_id"
        ),
        sa.ForeignKeyConstraint(
            ["start_id", "authorization_id", "workspace_id"],
            [
                "execution_start_attestations.id",
                "execution_start_attestations.authorization_id",
                "execution_start_attestations.workspace_id",
            ],
            name="fk_measurement_runs_start_authorization",
        ),
        sa.ForeignKeyConstraint(
            ["authorization_id", "contract_version_id", "experiment_id", "workspace_id"],
            [
                "execution_authorizations.id",
                "execution_authorizations.contract_version_id",
                "execution_authorizations.experiment_id",
                "execution_authorizations.workspace_id",
            ],
            name="fk_measurement_runs_authorization_contract",
        ),
        sa.UniqueConstraint(
            "workspace_id", "client_request_id", name="uq_measurement_runs_workspace_client_request_id"
        ),
        sa.UniqueConstraint(
            "id",
            "contract_version_id",
            "experiment_id",
            "workspace_id",
            name="uq_measurement_runs_id_contract_experiment_workspace",
        ),
        sa.UniqueConstraint("id", "start_id", "workspace_id", name="uq_measurement_runs_id_start_workspace"),
        sa.CheckConstraint(
            "declaration_semantics_version = 1", name="semantics_version_v1"
        ),
    )
    op.create_index("ix_experiment_measurement_runs_public_id", RUN_TABLE, ["public_id"], unique=True)
    op.create_index("ix_experiment_measurement_runs_workspace_id", RUN_TABLE, ["workspace_id"])
    op.create_index("ix_experiment_measurement_runs_experiment_id", RUN_TABLE, ["experiment_id"])
    op.create_index("ix_experiment_measurement_runs_authorization_id", RUN_TABLE, ["authorization_id"])
    op.create_index("ix_experiment_measurement_runs_start_id", RUN_TABLE, ["start_id"])
    op.create_index("ix_experiment_measurement_runs_contract_version_id", RUN_TABLE, ["contract_version_id"])

    op.create_table(
        SIGNAL_OUTPUT_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("required_signal_id", sa.Uuid(), nullable=False),
        sa.Column("contract_version_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("declaration_level", sa.String(20), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_measurement_signal_outputs"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_measurement_signal_outputs_workspace_id"
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "contract_version_id", "experiment_id", "workspace_id"],
            [
                f"{RUN_TABLE}.id",
                f"{RUN_TABLE}.contract_version_id",
                f"{RUN_TABLE}.experiment_id",
                f"{RUN_TABLE}.workspace_id",
            ],
            name="fk_measurement_signal_outputs_run_contract",
        ),
        sa.ForeignKeyConstraint(
            ["required_signal_id", "contract_version_id", "experiment_id", "workspace_id"],
            [
                "measurement_contract_signals.id",
                "measurement_contract_signals.contract_version_id",
                "measurement_contract_signals.experiment_id",
                "measurement_contract_signals.workspace_id",
            ],
            name="fk_measurement_signal_outputs_required_signal",
        ),
        sa.UniqueConstraint("run_id", "required_signal_id", name="uq_measurement_signal_outputs_run_signal"),
        sa.UniqueConstraint(
            "run_id",
            "required_signal_id",
            "workspace_id",
            name="uq_measurement_signal_outputs_run_signal_workspace",
        ),
        sa.UniqueConstraint("id", "workspace_id", name="uq_measurement_signal_outputs_id_workspace"),
        sa.CheckConstraint(
            "declaration_level IN ('DESCRIPTIVE', 'COMPARATIVE')",
            name="declaration_level_ok",
        ),
    )
    op.create_index("ix_experiment_measurement_signal_outputs_workspace_id", SIGNAL_OUTPUT_TABLE, ["workspace_id"])
    op.create_index("ix_experiment_measurement_signal_outputs_run_id", SIGNAL_OUTPUT_TABLE, ["run_id"])
    op.create_index(
        "ix_experiment_measurement_signal_outputs_required_signal_id", SIGNAL_OUTPUT_TABLE, ["required_signal_id"]
    )

    op.create_table(
        SLICE_OUTPUT_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("signal_output_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(100), nullable=False),
        sa.Column("qualifying_count", sa.Integer(), nullable=False),
        sa.Column("required_count", sa.Integer(), nullable=True),
        sa.Column("coverage_state", sa.String(20), nullable=True),
        sa.Column("pairing_state", sa.String(20), nullable=True),
        sa.Column("ambiguous_excluded_count", sa.Integer(), nullable=False),
        sa.Column("conflict_excluded_count", sa.Integer(), nullable=False),
        sa.Column("multi_signal_excluded_count", sa.Integer(), nullable=False),
        sa.Column("out_of_window_count", sa.Integer(), nullable=False),
        sa.Column("baseline_value", sa.Numeric(20, 4), nullable=True),
        sa.Column("observation_value", sa.Numeric(20, 4), nullable=True),
        sa.Column("signed_arithmetic_difference", sa.Numeric(20, 4), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_measurement_slice_outputs"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_measurement_slice_outputs_workspace_id"
        ),
        sa.ForeignKeyConstraint(
            ["signal_output_id", "workspace_id"],
            [f"{SIGNAL_OUTPUT_TABLE}.id", f"{SIGNAL_OUTPUT_TABLE}.workspace_id"],
            name="fk_measurement_slice_outputs_signal_output_workspace",
        ),
        sa.UniqueConstraint("signal_output_id", "channel", name="uq_measurement_slice_outputs_signal_channel"),
        sa.CheckConstraint(
            "coverage_state IS NULL OR coverage_state IN ('NOT_COVERED', 'COVERED')",
            name="coverage_state_valid",
        ),
        sa.CheckConstraint(
            "pairing_state IS NULL OR pairing_state IN ('INCOMPLETE', 'SURPLUS', 'LENGTH_MISMATCH', 'PAIR')",
            name="pairing_state_valid",
        ),
        sa.CheckConstraint(
            "(coverage_state IS NULL) != (pairing_state IS NULL)",
            name="coverage_xor_pairing",
        ),
        sa.CheckConstraint(
            "(required_count IS NOT NULL) = (coverage_state IS NOT NULL)",
            name="count_matches_state",
        ),
        sa.CheckConstraint(
            "required_count IS NULL OR required_count >= 1",
            name="required_count_min",
        ),
        sa.CheckConstraint(
            "qualifying_count >= 0", name="qualifying_nonneg"
        ),
        sa.CheckConstraint(
            "ambiguous_excluded_count >= 0", name="ambiguous_nonneg"
        ),
        sa.CheckConstraint(
            "conflict_excluded_count >= 0", name="conflict_nonneg"
        ),
        sa.CheckConstraint(
            "multi_signal_excluded_count >= 0",
            name="multi_signal_nonneg",
        ),
        sa.CheckConstraint(
            "out_of_window_count >= 0", name="out_of_window_nonneg"
        ),
        sa.CheckConstraint(
            "(baseline_value IS NULL) = (observation_value IS NULL) "
            "AND (observation_value IS NULL) = (signed_arithmetic_difference IS NULL)",
            name="pair_values_copresent",
        ),
        sa.CheckConstraint(
            "(pairing_state = 'PAIR') = (baseline_value IS NOT NULL)",
            name="pair_values_iff_pair",
        ),
    )
    op.create_index("ix_experiment_measurement_slice_outputs_workspace_id", SLICE_OUTPUT_TABLE, ["workspace_id"])
    op.create_index(
        "ix_experiment_measurement_slice_outputs_signal_output_id", SLICE_OUTPUT_TABLE, ["signal_output_id"]
    )

    op.create_table(
        DATUM_USAGE_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("start_id", sa.Uuid(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("required_signal_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(100), nullable=False),
        sa.Column("temporal_role", sa.String(20), nullable=True),
        sa.Column("usage_decision", sa.String(30), nullable=False),
        sa.Column("recorded_before_declaration", sa.Boolean(), nullable=False),
        sa.Column("later_grouping_entry_exists_at_run", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_measurement_datum_usages"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_measurement_datum_usages_workspace_id"
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "start_id", "workspace_id"],
            [f"{RUN_TABLE}.id", f"{RUN_TABLE}.start_id", f"{RUN_TABLE}.workspace_id"],
            name="fk_measurement_datum_usages_run_start",
        ),
        sa.ForeignKeyConstraint(
            ["claim_id", "start_id", "workspace_id"],
            [f"{CLAIM_TABLE}.id", f"{CLAIM_TABLE}.start_id", f"{CLAIM_TABLE}.workspace_id"],
            name="fk_measurement_datum_usages_claim_start",
        ),
        sa.ForeignKeyConstraint(
            ["claim_id", "required_signal_id", "workspace_id"],
            [f"{CLAIM_TABLE}.id", f"{CLAIM_TABLE}.required_signal_id", f"{CLAIM_TABLE}.workspace_id"],
            name="fk_measurement_datum_usages_claim_signal",
        ),
        # NOTE: no FK to experiment_measurement_signal_outputs here —
        # removed as an implementation-time finding; see the identical note
        # in app/strategy/models.py::ExperimentMeasurementDatumUsage.
        sa.UniqueConstraint("run_id", "claim_id", name="uq_measurement_datum_usages_run_claim"),
        sa.CheckConstraint(
            "usage_decision IN "
            "('CONSUMED', 'EXCLUDED_AMBIGUOUS', 'EXCLUDED_OUT_OF_WINDOW', "
            "'EXCLUDED_MULTI_SIGNAL', 'EXCLUDED_CONFLICT')",
            name="usage_decision_valid",
        ),
        sa.CheckConstraint(
            "temporal_role IS NULL OR temporal_role IN ('BASELINE', 'OBSERVATION')",
            name="temporal_role_valid",
        ),
        sa.CheckConstraint(
            "temporal_role IS NULL OR usage_decision IN ('CONSUMED', 'EXCLUDED_MULTI_SIGNAL', 'EXCLUDED_CONFLICT')",
            name="role_matches_decision",
        ),
    )
    op.create_index("ix_experiment_measurement_datum_usages_workspace_id", DATUM_USAGE_TABLE, ["workspace_id"])
    op.create_index("ix_experiment_measurement_datum_usages_run_id", DATUM_USAGE_TABLE, ["run_id"])
    op.create_index("ix_experiment_measurement_datum_usages_start_id", DATUM_USAGE_TABLE, ["start_id"])
    op.create_index("ix_experiment_measurement_datum_usages_claim_id", DATUM_USAGE_TABLE, ["claim_id"])
    op.create_index(
        "ix_experiment_measurement_datum_usages_required_signal_id", DATUM_USAGE_TABLE, ["required_signal_id"]
    )

    # Additive audit FK, matching the identical pattern already used for every
    # other governed capability's own creation event.
    op.add_column("audit_events", sa.Column("experiment_measurement_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_audit_events_experiment_measurement_run_id",
        "audit_events",
        RUN_TABLE,
        ["experiment_measurement_run_id"],
        ["id"],
    )
    op.create_index(
        "ix_audit_events_experiment_measurement_run_id", "audit_events", ["experiment_measurement_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_experiment_measurement_run_id", table_name="audit_events")
    op.drop_constraint("fk_audit_events_experiment_measurement_run_id", "audit_events", type_="foreignkey")
    op.drop_column("audit_events", "experiment_measurement_run_id")

    # Reverse creation order exactly: experiment_measurement_datum_usages
    # (dropped here, with the whole table) is the only thing whose FKs
    # depend on the two claim candidate keys, so those keys are dropped last.
    op.drop_table(DATUM_USAGE_TABLE)
    op.drop_table(SLICE_OUTPUT_TABLE)
    op.drop_table(SIGNAL_OUTPUT_TABLE)
    op.drop_table(RUN_TABLE)

    op.drop_constraint("uq_experiment_evidence_claims_id_required_signal_workspace", CLAIM_TABLE, type_="unique")
    op.drop_constraint("uq_experiment_evidence_claims_id_start_workspace", CLAIM_TABLE, type_="unique")
