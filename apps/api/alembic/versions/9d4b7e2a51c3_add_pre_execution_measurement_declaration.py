"""Pre-Execution Measurement Declaration (Model A + D, no new aggregate); additive only, no backfill.

Adds nullable structured-declaration columns to ``measurement_contract_versions``
and ``measurement_contract_signals``, the row-local CHECK backstops for the
DB-expressible invariants, and the binding-slot unique expression index. Every
existing row stays all-NULL (LEGACY / UNSPECIFIED): nothing is backfilled,
rewritten or reinterpreted, and every new CHECK is satisfied by an all-NULL row.

DOWNGRADE HAZARD (accepted): downgrading drops the new columns, so any
STRUCTURED declaration data written after this revision is LOST. The downgrade
is schema-reversible, not semantically lossless.
"""
from alembic import op
import sqlalchemy as sa

revision = "9d4b7e2a51c3"
down_revision = "7c1e9a4d2b68"
branch_labels = None
depends_on = None

CONTRACT_TABLE = "measurement_contract_versions"
SIGNAL_TABLE = "measurement_contract_signals"
BINDING_SLOT_INDEX = "uq_contract_signals_binding_slot"

CONTRACT_CHECKS = (
    (
        "declaration_level_valid",
        "declaration_level IS NULL OR declaration_level IN ('DESCRIPTIVE', 'COMPARATIVE')",
    ),
    (
        "level_version_copresent",
        "(declaration_level IS NULL) = (declaration_semantics_version IS NULL)",
    ),
    (
        "semantics_version_v1",
        "declaration_semantics_version IS NULL OR declaration_semantics_version = 1",
    ),
    (
        "structured_window_bounds",
        "declaration_level IS NULL OR (measurement_window_days IS NOT NULL "
        "AND measurement_window_days BETWEEN 4 AND 3650)",
    ),
    (
        "baseline_iff_comparative",
        "(baseline_window_days IS NOT NULL) = COALESCE(declaration_level = 'COMPARATIVE', false)",
    ),
    (
        "baseline_window_bounds",
        "baseline_window_days IS NULL OR baseline_window_days BETWEEN 4 AND 3650",
    ),
)

SIGNAL_CHECKS = (
    ("binding_copresent", "(bound_metric_name IS NULL) = (channel_binding IS NULL)"),
    ("channel_binding_valid", "channel_binding IS NULL OR channel_binding IN ('ANY', 'EXACT')"),
    (
        "bound_channel_consistent",
        "CASE channel_binding WHEN 'EXACT' THEN bound_channel IS NOT NULL ELSE bound_channel IS NULL END",
    ),
    (
        "binding_text_nonblank_trimmed",
        "(bound_metric_name IS NULL OR (char_length(btrim(bound_metric_name)) > 0 "
        "AND bound_metric_name = btrim(bound_metric_name))) AND "
        "(bound_channel IS NULL OR (char_length(btrim(bound_channel)) > 0 "
        "AND bound_channel = btrim(bound_channel)))",
    ),
    ("min_data_points_positive", "min_data_points IS NULL OR min_data_points >= 1"),
    ("min_points_requires_binding", "min_data_points IS NULL OR bound_metric_name IS NOT NULL"),
)


def upgrade() -> None:
    op.add_column(CONTRACT_TABLE, sa.Column("declaration_level", sa.String(20), nullable=True))
    op.add_column(CONTRACT_TABLE, sa.Column("declaration_semantics_version", sa.Integer(), nullable=True))
    op.add_column(CONTRACT_TABLE, sa.Column("baseline_window_days", sa.Integer(), nullable=True))

    op.add_column(SIGNAL_TABLE, sa.Column("bound_metric_name", sa.String(100), nullable=True))
    op.add_column(SIGNAL_TABLE, sa.Column("channel_binding", sa.String(10), nullable=True))
    op.add_column(SIGNAL_TABLE, sa.Column("bound_channel", sa.String(100), nullable=True))
    op.add_column(SIGNAL_TABLE, sa.Column("min_data_points", sa.Integer(), nullable=True))

    for name, condition in CONTRACT_CHECKS:
        op.create_check_constraint(name, CONTRACT_TABLE, condition)
    for name, condition in SIGNAL_CHECKS:
        op.create_check_constraint(name, SIGNAL_TABLE, condition)

    op.create_index(
        BINDING_SLOT_INDEX,
        SIGNAL_TABLE,
        [
            "contract_version_id",
            "bound_metric_name",
            "channel_binding",
            sa.text("COALESCE(bound_channel, '')"),
        ],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(BINDING_SLOT_INDEX, table_name=SIGNAL_TABLE)
    for name, _condition in reversed(SIGNAL_CHECKS):
        op.drop_constraint(name, SIGNAL_TABLE, type_="check")
    for name, _condition in reversed(CONTRACT_CHECKS):
        op.drop_constraint(name, CONTRACT_TABLE, type_="check")

    for column in ("min_data_points", "bound_channel", "channel_binding", "bound_metric_name"):
        op.drop_column(SIGNAL_TABLE, column)
    for column in ("baseline_window_days", "declaration_semantics_version", "declaration_level"):
        op.drop_column(CONTRACT_TABLE, column)
