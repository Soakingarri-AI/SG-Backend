"""Ask SoakinGarri request/response contract.

Served at ``/api/v1/ask``. ``sources`` is always present and always a valid
list — it is simply empty while the RAG vector index is still being built, so
frontends can render the citation panel unconditionally.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class LearningMode(str, Enum):
    beginner = "beginner"
    normal = "normal"
    advanced = "advanced"


class AskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    session_id: uuid.UUID | None = None
    learning_mode: LearningMode = LearningMode.normal


class AskSource(BaseModel):
    """One retrieved citation shown alongside the answer."""

    title: str
    source_url: str | None = None
    snippet: str
    category: str


class AskResponse(BaseModel):
    session_id: uuid.UUID
    message_id: uuid.UUID
    answer: str
    learning_mode: LearningMode
    sources: list[AskSource]


class AskSessionListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class AskMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    meta: dict
    created_at: datetime


class AskSessionDetailResponse(BaseModel):
    id: uuid.UUID
    title: str | None
    learning_mode: LearningMode
    created_at: datetime
    updated_at: datetime
    messages: list[AskMessage]
