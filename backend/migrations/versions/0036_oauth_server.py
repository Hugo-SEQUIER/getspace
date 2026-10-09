"""Parameters: the connection grants and principals from earlier migrations.
What it does: adds OAuth clients, authorization requests and tokens for MCP clients that
connect through a browser, reachable only through definer functions.
Output: storage for the self-hosted authorization server in space.oauth_server.
"""

from pathlib import Path

from alembic import op

revision = "0036_oauth_server"
down_revision = "0035_selfhosted_limits"
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
