"""Parameters: the existing memory resources and workspace items.
What it does: installs an identifier-only index of routing rules and their containers.
Output: the Space map can show placement rules without decrypting all memory.
"""

from pathlib import Path

from alembic import op

revision = "0029_memory_routes"
down_revision = "0028_classification_scale"
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
