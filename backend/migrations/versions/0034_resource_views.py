"""Parameters: resources and principals from earlier migrations.
What it does: records which revision of each document or task a signed-in person last opened.
Output: the website can show what agents changed since the person last looked.
"""

from pathlib import Path

from alembic import op

revision = "0034_resource_views"
down_revision = "0033_api_keys"
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
