"""Factorizer — generated 15-section factory setup plans."""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDMixin


class FactoryPlan(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "factory_plans"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Wizard inputs: product, category, budget, automation_level, region, materials[].
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    target_product: Mapped[str] = mapped_column(String(200), nullable=False)
    plan_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured extracts powering the MachineList / ProcessFlow / CostBreakdown UI.
    machines: Mapped[list] = mapped_column(JSONB, default=list)
    process_flow: Mapped[list] = mapped_column(JSONB, default=list)
    cost_breakdown: Mapped[list] = mapped_column(JSONB, default=list)
    pdf_s3_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
