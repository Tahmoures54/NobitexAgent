"""Add encrypted TOTP authenticator state to users.

Revision ID: 0004_totp_auth
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_totp_auth"
down_revision: Union[str, None] = "0003_external_identity_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret_encrypted", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    with op.batch_alter_table("users") as batch:
        batch.alter_column("totp_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_secret_encrypted")
