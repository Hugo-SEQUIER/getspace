"""Parameters: the Spaces, resources and storage accounting from earlier migrations.
What it does: adds the ledger of file parts a document's original file is stored in, and
counts those parts towards storage.
Output: a document can keep its original PDF, Word, PowerPoint or other file.
"""

from pathlib import Path

from alembic import op

revision = "0041_document_files"
down_revision = "0040_webhook_inbox"
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
