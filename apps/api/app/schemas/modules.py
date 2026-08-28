"""Request/response + structured-output schemas for the six modules.

The ``*Structured`` models double as validation contracts passed to
``ai_service.complete_json`` so the LLM output is guaranteed well-formed.
"""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

# Ask SoakinGarri schemas live in ``app/schemas/ask.py``.


# -------------------------------- ExamFlow -------------------------------- #
class ExamGenerateRequest(BaseModel):
    board: Literal["WAEC", "JAMB", "NECO", "COMMON_ENTRANCE"]
    subject: str
    num_questions: int = Field(default=20, ge=5, le=100)
    years: list[int] | None = None
    duration_seconds: int = Field(default=3600, ge=300, le=14400)


class ExamAnswerSubmit(BaseModel):
    answers: dict[uuid.UUID, str]  # session_question_id -> chosen option key


# ------------------------------ AfroSimulator ----------------------------- #
class SimulationCreate(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    cultures: list[Literal["yoruba", "igbo", "hausa"]] = Field(min_length=2)
    max_turns: int = Field(default=8, ge=2, le=20)


class SimulationTurnOut(BaseModel):
    turn_index: int
    culture: str
    speaker: str
    content: str
    proverbs: list[str]


# ------------------------------- Meme Generator --------------------------- #
class MemeRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=500)
    style: str = "nigerian_student"


class MemeStructured(BaseModel):
    """Strict LLM JSON contract. Reality must be the polar opposite of expectation."""

    expectation: str = Field(description="The optimistic assumption")
    reality: str = Field(description="The polar-opposite disappointing truth")
    caption: str = Field(description="Punchy shareable caption")


# ------------------------------- InfiniteParts ---------------------------- #
class PartRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=800)


class PartParameters(BaseModel):
    """Validated parametric envelope returned to the Three.js preview."""

    name: str
    units: Literal["mm", "cm", "in"] = "mm"
    length: float = Field(gt=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    hole_diameter: float = Field(ge=0, default=0)
    wall_thickness: float = Field(gt=0, default=2.0)
    tolerance: float = Field(ge=0, default=0.1)
    features: list[str] = Field(default_factory=list)


# --------------------------------- Factorizer ----------------------------- #
class FactoryWizardInput(BaseModel):
    target_product: str
    category: str
    budget_usd: float = Field(gt=0)
    automation_level: Literal["manual", "semi_automated", "fully_automated"]
    region: str
    raw_materials: list[str] = Field(default_factory=list)


class Machine(BaseModel):
    name: str
    purpose: str
    estimated_cost_usd: float
    quantity: int = 1


class CostItem(BaseModel):
    category: str
    amount_usd: float


class FactoryPlanStructured(BaseModel):
    machines: list[Machine]
    process_flow: list[str]
    cost_breakdown: list[CostItem]
