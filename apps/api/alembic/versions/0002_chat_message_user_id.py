"""chat_messages: add denormalized user_id owner column

Ask SoakinGarri (and future tools) stamp each message with its author so
per-user queries and audits do not need the session join. Nullable because
rows written before this migration have no owner recorded.

Revision ID: 0002_chat_message_user_id
Revises: 0001_initial
Create Date: 2026-08-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0002_chat_message_user_id"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("user_id", PgUUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_chat_messages_user_id_users",
        "chat_messages",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_chat_messages_user_id", "chat_messages", ["user_id"])

    # Backfill owners for existing rows from their parent session.
    op.execute(
        """
        UPDATE chat_messages m
        SET user_id = s.user_id
        FROM chat_sessions s
        WHERE m.session_id = s.id AND m.user_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_user_id", table_name="chat_messages")
    op.drop_constraint(
        "fk_chat_messages_user_id_users", "chat_messages", type_="foreignkey"
    )
    op.drop_column("chat_messages", "user_id")
