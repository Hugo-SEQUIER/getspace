"""Parameters: the existing encrypted workspace item revisions and memory job queue.
What it does: installs an encrypted catalog embedding cache and a reclassification job kind.
Output: large catalogs are shortlisted without a readable vector index, and old claims
left unassigned by the 32-item limit can be caught up in the background.
"""

from pathlib import Path

from alembic import op

revision = "0028_classification_scale"
down_revision = "0027_webhooks"
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
