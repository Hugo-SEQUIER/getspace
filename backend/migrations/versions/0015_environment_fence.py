"""Operator-managed environment identity and recovery fence."""

from pathlib import Path

from alembic import op

revision = "0015_environment_fence"
down_revision = "0014_usage_limits"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(), execution_options={"no_parameters": True}
    )


def downgrade():
    raise RuntimeError("Destructive downgrade is unsupported; use a verified recovery")
