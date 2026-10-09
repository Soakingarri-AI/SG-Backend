"""attachments: user files attached to chat messages

Revision ID: 0004_attachments
Revises: 0003_user_email_verification
Create Date: 2026-10-09
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0004_attachments"
down_revision: Union[str, None] = "0003_user_email_verification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KIND_VALUES = ("document", "image")
_STATUS_VALUES = ("ok", "empty", "truncated", "unsupported", "failed")

# The types are created explicitly below, so the column definitions must not
# try to emit CREATE TYPE a second time inside create_table.
_KIND = postgresql.ENUM(*_KIND_VALUES, name="attachment_kind", create_type=False)
_STATUS = postgresql.ENUM(
    *_STATUS_VALUES, name="extraction_status", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    sa.Enum(*_KIND_VALUES, name="attachment_kind").create(bind, checkfirst=True)
    sa.Enum(*_STATUS_VALUES, name="extraction_status").create(bind, checkfirst=True)

    op.create_table(
        "attachments",
        sa.Column(
            "id", PgUUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")
        ),
        sa.Column(
            "user_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(127), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False, unique=True),
        sa.Column("kind", _KIND, nullable=False),
        sa.Column("extraction_status", _STATUS, nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("meta", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_attachments_user_id", "attachments", ["user_id"])
    op.create_index("ix_attachments_session_id", "attachments", ["session_id"])
    op.create_index(
        "ix_attachments_user_created", "attachments", ["user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_attachments_user_created", table_name="attachments")
    op.drop_index("ix_attachments_session_id", table_name="attachments")
    op.drop_index("ix_attachments_user_id", table_name="attachments")
    op.drop_table("attachments")
    bind = op.get_bind()
    sa.Enum(name="extraction_status").drop(bind, checkfirst=True)
    sa.Enum(name="attachment_kind").drop(bind, checkfirst=True)
