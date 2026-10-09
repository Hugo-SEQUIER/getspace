"""Durable encrypted projects and custom database snapshots."""

from pathlib import Path

from alembic import op

revision = "0012_workspace_items"
down_revision = "0011_agent_identities"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(), execution_options={"no_parameters": True}
    )


def downgrade():
    raise RuntimeError("Use a verified backup restore; destructive downgrades are unsupported")
