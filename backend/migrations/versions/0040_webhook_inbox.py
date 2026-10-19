"""Parameters: webhook subscriptions and the outbox from earlier migrations.
What it does: adds the agent inbox delivery kind and owner-scoped inbox receipt functions.
Output: agents drain inbox deliveries over MCP; endpoint deliveries keep the HTTPS worker.
"""

from pathlib import Path

from alembic import op

revision = "0040_webhook_inbox"
down_revision = "0039_profile_images"
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
