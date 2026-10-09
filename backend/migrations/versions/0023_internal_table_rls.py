"""Parameters: Existing Space database.
What it does: Enables RLS on internal operator and migration tables.
Output: Four protected tables with existing runtime/owner access preserved.
"""

from pathlib import Path

from alembic import op

revision = "0023_internal_table_rls"
down_revision = "0022_memory_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Parameters: none. What it does: applies internal-table RLS. Output: none."""
    op.get_bind().exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(), execution_options={"no_parameters": True}
    )


def downgrade() -> None:
    """Parameters: none. What it does: refuses weakened protection. Output: error."""
    raise RuntimeError("Use a reviewed forward migration")
