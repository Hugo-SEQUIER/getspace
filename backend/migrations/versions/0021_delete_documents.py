"""Allow scoped document deletion and durable object cleanup."""

from pathlib import Path

from alembic import op

revision = "0021_delete_documents"
down_revision = "0020_delete_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(), execution_options={"no_parameters": True}
    )


def downgrade() -> None:
    raise RuntimeError("Use a reviewed forward migration")
