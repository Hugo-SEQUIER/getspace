"""Index opaque relationships between current encrypted memory claims."""

from pathlib import Path

from alembic import op

revision = "0022_memory_links"
down_revision = "0021_delete_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Parameters: none. What it does: adds the link index. Output: none."""
    op.get_bind().exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(), execution_options={"no_parameters": True}
    )


def downgrade() -> None:
    """Parameters: none. What it does: refuses destructive rollback. Output: error."""
    raise RuntimeError("Use a reviewed forward migration")
