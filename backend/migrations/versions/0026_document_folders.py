"""Parameters: the existing workspace item kind constraint.
What it does: permits independent document folders without changing stored items.
Output: folders share the existing encrypted revisions and Space-scoped policies.
"""

from pathlib import Path

from alembic import op

revision = "0026_document_folders"
down_revision = "0025_task_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Parameters: none. What it does: permits folder items. Output: none."""
    op.get_bind().exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(), execution_options={"no_parameters": True}
    )


def downgrade() -> None:
    """Parameters: none. What it does: refuses destructive rollback. Output: error."""
    raise RuntimeError("Use a reviewed forward migration")
