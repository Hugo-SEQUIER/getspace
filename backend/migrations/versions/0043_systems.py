"""Parameters: the workspace item kinds from `0026_document_folders` and the head
`0042_database_records` left.
What it does: adds the system and lab entry kinds of the Systems area.
Output: a Space can store its recurring systems and the hypotheses and lessons they test.
"""

from pathlib import Path

from alembic import op

revision = "0043_systems"
down_revision = "0042_database_records"
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
