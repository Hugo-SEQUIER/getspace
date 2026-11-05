"""Parameters: the pending caps of `0032_device_connections` and `0036_oauth_server`, and the
head `0043_systems` left.
What it does: counts only requests that can still be answered toward the caps, and lets the
owner's direct connection past the device cap.
Output: an expired or anonymous flood no longer keeps the owner from connecting an agent.
"""

from pathlib import Path

from alembic import op

revision = "0044_pending_caps"
down_revision = "0043_systems"
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
