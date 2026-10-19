"""Parameters: Spaces from earlier migrations.
What it does: stamps each new revision with the Space content revision it commits under.
Output: an export reads the Space as it was at its first page while writes continue.
"""

from pathlib import Path

from alembic import op

revision = "0038_export_snapshots"
down_revision = "0037_change_revisions"
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
