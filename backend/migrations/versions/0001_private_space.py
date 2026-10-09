"""Private identity, personal ownership and encrypted Space/key registry.

Revision ID: 0001_private_space
"""

from pathlib import Path

from alembic import op

revision = "0001_private_space"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).with_suffix(".sql").read_text()
    # The versioned SQL is trusted repository code, not user-supplied statements.
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is not supported; use a reviewed forward migration")
