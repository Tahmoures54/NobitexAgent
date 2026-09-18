"""Add trailing-stop state to paper trades.

Revision ID: 0002_trailing_stop
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_trailing_stop"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("highest_price", sa.Float(), nullable=True))
    op.add_column("trades", sa.Column("lowest_price", sa.Float(), nullable=True))
    op.add_column("trades", sa.Column("trailing_stop", sa.Float(), nullable=True))
    op.add_column(
        "trades",
        sa.Column("trailing_active", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("trades", "trailing_active", server_default=None)


def downgrade() -> None:
    op.drop_column("trades", "trailing_active")
    op.drop_column("trades", "trailing_stop")
    op.drop_column("trades", "lowest_price")
    op.drop_column("trades", "highest_price")
