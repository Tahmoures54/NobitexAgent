"""Migrate users from password auth to external identity auth.

Revision ID: 0003_external_identity_auth
Replaces legacy password-only account fields with provider identity,
profile fields, and a unique provider/subject pair.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_external_identity_auth"
down_revision: Union[str, None] = "0002_trailing_stop"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("auth_provider", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("auth_subject", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("full_name", sa.String(120), nullable=True))
    op.add_column("users", sa.Column("phone_number", sa.String(32), nullable=True))

    # Existing accounts are retained. They must authenticate once through a
    # supported provider with the same email before they can be used again.
    op.execute(
        sa.text(
            "UPDATE users SET auth_provider = 'legacy', auth_subject = 'legacy:' || CAST(id AS TEXT) "
            "WHERE auth_provider IS NULL"
        )
    )

    with op.batch_alter_table("users") as batch:
        batch.alter_column("password_hash", existing_type=sa.String(255), nullable=True)
        batch.alter_column("auth_provider", existing_type=sa.String(32), nullable=False)
        batch.alter_column("auth_subject", existing_type=sa.String(255), nullable=False)
        batch.create_unique_constraint(
            "uq_users_auth_provider_subject",
            ["auth_provider", "auth_subject"],
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_auth_provider_subject", type_="unique")
        batch.alter_column("password_hash", existing_type=sa.String(255), nullable=False)
        batch.drop_column("phone_number")
        batch.drop_column("full_name")
        batch.drop_column("auth_subject")
        batch.drop_column("auth_provider")
