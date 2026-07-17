"""initial schema — extensions + all platform tables

Bootstrap migration. Enables the pgvector + uuid-ossp extensions, then creates
the full schema from the registered SQLAlchemy metadata. Subsequent schema
changes should use ``alembic revision --autogenerate`` and explicit op.* calls.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import op

from app.core.database import Base
import app.models  # noqa: F401  registers all tables on Base.metadata

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
