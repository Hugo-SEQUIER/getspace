"""Parameters: the file-part ledger from `0041_document_files` and the head
`0043_systems` left.
What it does: moves a part's bytes out of the row and into an object on the server's disk,
named by `object_key`; a row keeps either its bytes or a key, never both and never neither.
Output: a document's original file no longer counts against PostgreSQL's size.
"""

from pathlib import Path

from alembic import op

revision = "0044_file_objects"
down_revision = "0043_systems"
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
