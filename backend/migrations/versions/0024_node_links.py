"""Parameters: existing Space schema.
What it does: separates explicit node links from hash-pinned inferred claim links.
Output: resource-scoped indexes with forced tenant RLS.
"""

from pathlib import Path

from alembic import op

revision = "0024_node_links"
down_revision = "0023_internal_table_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Parameters: none. What it does: adds durable node links. Output: none."""
    op.get_bind().exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(), execution_options={"no_parameters": True}
    )


def downgrade() -> None:
    """Parameters: none. What it does: refuses destructive rollback. Output: error."""
    raise RuntimeError("Use a reviewed forward migration")
