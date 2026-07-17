"""AfroSimulator — cultural multi-agent personas + simulation runs."""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDMixin


class Culture(str, enum.Enum):
    yoruba = "yoruba"
    igbo = "igbo"
    hausa = "hausa"


class SimulationStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class AgentPersona(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "agent_personas"

    culture: Mapped[Culture] = mapped_column(
        SAEnum(Culture, name="culture", native_enum=True), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # Rolling memory buffer + linguistic schedule (greetings, proverbs, code-switch).
    memory_buffer: Mapped[list] = mapped_column(JSONB, default=list)
    linguistic_schedule: Mapped[dict] = mapped_column(JSONB, default=dict)


class Simulation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "simulations"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    topic: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[SimulationStatus] = mapped_column(
        SAEnum(SimulationStatus, name="simulation_status", native_enum=True),
        default=SimulationStatus.pending,
    )
    max_turns: Mapped[int] = mapped_column(Integer, default=8)
    participant_cultures: Mapped[list[str]] = mapped_column(JSONB, default=list)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    turns: Mapped[list["SimulationTurn"]] = relationship(
        back_populates="simulation",
        cascade="all, delete-orphan",
        order_by="SimulationTurn.turn_index",
    )


class SimulationTurn(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "simulation_turns"

    simulation_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        index=True,
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    culture: Mapped[Culture] = mapped_column(SAEnum(Culture, name="culture"))
    speaker: Mapped[str] = mapped_column(String(120), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    proverbs: Mapped[list[str]] = mapped_column(JSONB, default=list)
    safety_flags: Mapped[dict] = mapped_column(JSONB, default=dict)

    simulation: Mapped["Simulation"] = relationship(back_populates="turns")
