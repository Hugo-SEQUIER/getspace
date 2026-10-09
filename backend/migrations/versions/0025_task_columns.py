"""Parameters: the existing canonical Tasks collection.
What it does: permits scoped updates to its encrypted status options.
Output: a column-level runtime grant; existing RLS remains unchanged.
"""

from pathlib import Path

from alembic import op

revision = "0025_task_columns"
down_revision = "0024_node_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Parameters: none. What it does: grants encrypted schema updates. Output: none."""
    op.get_bind().exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(), execution_options={"no_parameters": True}
    )


def downgrade() -> None:
    """Parameters: none. What it does: refuses destructive rollback. Output: error."""
    raise RuntimeError("Use a reviewed forward migration")
