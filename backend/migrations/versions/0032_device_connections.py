"""Parameters: the connection grants and agent identities from earlier migrations.
What it does: adds device-code connection requests and their definer functions.
Output: agents without a browser callback can connect through a six-digit code.
"""

from pathlib import Path

from alembic import op

revision = "0032_device_connections"
down_revision = "0029_memory_routes"
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
