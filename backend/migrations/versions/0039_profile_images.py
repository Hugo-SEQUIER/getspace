"""Parameters: principals, Spaces and Space keys from earlier migrations.
What it does: adds one encrypted profile picture per person in their personal Space.
Output: the website can show a picture the person chose instead of their sign-in photo.
"""

from pathlib import Path

from alembic import op

revision = "0039_profile_images"
down_revision = "0038_export_snapshots"
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
