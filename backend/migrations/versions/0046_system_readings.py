"""Parameters: the workspace item kinds from `0043_systems` and the head
`0045_pending_caps` left.
What it does: adds the month-of-readings kind of the Systems area.
Output: a system can carry the dated numbers its sensor scripts record.
"""

from pathlib import Path

from alembic import op

revision = "0046_system_readings"
down_revision = "0045_pending_caps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Parameters: none. What it does: applies the reviewed SQL. Output: none."""
    op.get_bind().exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(), execution_options={"no_parameters": True}
    )


def downgrade() -> None:
    """Parameters: none. What it does: refuses destructive rollback. Output: error."""
    raise RuntimeError("Use a reviewed forward migration")
