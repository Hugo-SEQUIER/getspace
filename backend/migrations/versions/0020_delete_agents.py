"""Remove agent profiles from the active list while preserving attribution."""

from pathlib import Path

from alembic import op

revision = "0020_delete_agents"
down_revision = "0019_overview"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(), execution_options={"no_parameters": True}
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is not supported; use a reviewed forward migration")
