"""Parameters: the workspace item tables from `0012_workspace_items` and the storage trigger and
head `0041_document_files` left.
What it does: adds one packed row per database record, with its own history, and counts those
rows toward retained storage.
Output: a database edit stores the rows it changed instead of a copy of the whole table.
"""

from pathlib import Path

from alembic import op

revision = "0042_database_records"
down_revision = "0041_document_files"
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
