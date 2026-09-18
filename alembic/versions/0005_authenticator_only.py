"""Remove retired password and external OAuth columns from users.

Revision ID: 0005_authenticator_only
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_authenticator_only"
down_revision: Union[str, None] = "0004_totp_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_auth_provider_subject", type_="unique")
        batch.drop_column("auth_subject")
        batch.drop_column("auth_provider")
        batch.drop_column("password_hash")


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("password_hash", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("auth_provider", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("auth_subject", sa.String(length=255), nullable=True))
        batch.create_unique_constraint(
            "uq_users_auth_provider_subject",
            ["auth_provider", "auth_subject"],
        )
