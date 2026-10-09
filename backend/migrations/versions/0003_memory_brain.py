"""Autonomous memory brain: a pgvector index for encrypted memory claims."""

from pathlib import Path

from alembic import op

revision = "0003_memory_brain"
down_revision = "0002_resources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(), execution_options={"no_parameters": True}
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is not supported; use a reviewed forward migration")
