"""Parameters: principals, Spaces and the device connections from earlier migrations.
What it does: adds personal API keys and their definer functions.
Output: a person's own clients can call the REST API with a revocable Space-bound key.
"""

from pathlib import Path

from alembic import op

revision = "0033_api_keys"
down_revision = "0032_device_connections"
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
