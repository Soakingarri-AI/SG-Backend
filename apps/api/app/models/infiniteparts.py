"""InfiniteParts — parametric mechanical part specifications."""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDMixin


class PartSpecification(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "part_specifications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Validated parametric envelope: length, width, height, hole_diameter,
    # wall_thickness, tolerances, units, features[].
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    stl_s3_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
