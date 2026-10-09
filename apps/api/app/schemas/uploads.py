"""Chat attachment contract.

``kind`` tells the frontend how to render the item: ``image`` gets a thumbnail,
``document`` gets a file chip. ``extraction_status`` is what to warn about —
``empty`` on a scanned PDF is the common case worth surfacing, because the
model will not be able to read it.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    kind: str
    extraction_status: str
    session_id: uuid.UUID | None = None
    created_at: datetime


class AttachmentDetail(AttachmentRead):
    """Adds a preview of the text the model will actually see."""

    text_preview: str | None = None
    meta: dict = Field(default_factory=dict)


class UploadLimits(BaseModel):
    """Lets the UI enforce the same rules as the API before sending anything."""

    max_bytes: int
    max_files_per_message: int
    quota_bytes: int
    used_bytes: int
    accepted_types: list[str]
