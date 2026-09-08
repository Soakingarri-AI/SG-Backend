"""users: add email verification columns

Adds ``is_verified`` / ``verified_at``. Existing accounts are backfilled as
verified: they registered before verification existed, and with
REQUIRE_EMAIL_VERIFICATION on, defaulting them to false would lock every
current user out of the platform.

Revision ID: 0003_user_email_verification
Revises: 0002_chat_message_user_id
Create Date: 2026-09-08
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_user_email_verification"
down_revision: Union[str, None] = "0002_chat_message_user_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_verified", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "users",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Grandfather every pre-existing account so nobody is locked out.
    op.execute(
        "UPDATE users SET is_verified = true, verified_at = now() "
        "WHERE is_verified = false"
    )

    # New rows get their value from the application, not the database.
    op.alter_column("users", "is_verified", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "verified_at")
    op.drop_column("users", "is_verified")
