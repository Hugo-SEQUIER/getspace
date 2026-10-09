"""Parameters: the existing encrypted Space schema.
What it does: installs owner-scoped outbound webhook storage and worker privileges.
Output: durable subscriptions and deliveries, without provisioning a login or starting work.
"""

from pathlib import Path

from alembic import op

revision = "0027_webhooks"
down_revision = "0026_document_folders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Parameters: none. What it does: applies the webhook schema. Output: none."""
    op.get_bind().exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(), execution_options={"no_parameters": True}
    )


def downgrade() -> None:
    """Parameters: none. What it does: refuses destructive rollback. Output: error."""
    raise RuntimeError("Use a reviewed forward migration")
