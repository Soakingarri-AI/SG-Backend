"""Upload orchestration: validate, store, extract, record.

Keeps the router thin and gives the Ask pipeline one place to resolve
attachment ids into prompt-ready content.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import PurePosixPath

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.attachment import Attachment, AttachmentKind, ExtractionStatus
from app.services import file_extraction
from app.services.file_extraction import UnsupportedFileError
from app.services.storage_service import StorageError, build_key, storage

logger = logging.getLogger(__name__)

_MAX_FILENAME = 255


def safe_filename(raw: str | None) -> str:
    """A display-only filename: no directories, no control characters."""
    name = PurePosixPath((raw or "").replace("\\", "/")).name
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    # Leading dots would hide the file in listings and read as an extension.
    name = name.lstrip(".") or "upload"
    return name[:_MAX_FILENAME]


async def used_bytes(db: AsyncSession, user_id: uuid.UUID) -> int:
    total = await db.scalar(
        select(func.coalesce(func.sum(Attachment.size_bytes), 0)).where(
            Attachment.user_id == user_id
        )
    )
    return int(total or 0)


async def create(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    data: bytes,
    filename: str | None,
) -> Attachment:
    """Validate and persist one upload. Raises HTTPException on rejection."""
    if not data:
        raise HTTPException(
            422, "The uploaded file is empty"
        )
    if len(data) > settings.UPLOAD_MAX_BYTES:
        limit_mb = settings.UPLOAD_MAX_BYTES // (1024 * 1024)
        raise HTTPException(
            413,
            f"File is larger than the {limit_mb} MB limit",
        )

    if await used_bytes(db, user_id) + len(data) > settings.UPLOAD_USER_QUOTA_BYTES:
        raise HTTPException(
            413,
            "You have reached your upload storage limit. Delete older files to "
            "free up space.",
        )

    name = safe_filename(filename)
    try:
        result = file_extraction.extract(data, name)
    except UnsupportedFileError as exc:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)
        ) from exc

    key = build_key(str(user_id), name)
    try:
        storage.save(key, data, result.content_type)
    except StorageError as exc:
        logger.error("Storing upload %s failed: %s", key, exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "File storage is unavailable right now. Please try again.",
        ) from exc

    attachment = Attachment(
        user_id=user_id,
        filename=name,
        content_type=result.content_type,
        size_bytes=len(data),
        storage_key=key,
        kind=result.kind,
        extraction_status=result.status,
        extracted_text=result.text,
        meta=result.meta,
    )
    db.add(attachment)
    await db.flush()
    await db.refresh(attachment)
    return attachment


async def get_owned(
    db: AsyncSession, attachment_id: uuid.UUID, user_id: uuid.UUID
) -> Attachment:
    """Load an attachment; 404 when unknown, 403 when owned by someone else."""
    attachment = await db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    if attachment.user_id != user_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You do not have access to this attachment"
        )
    return attachment


async def resolve_for_message(
    db: AsyncSession,
    *,
    attachment_ids: list[uuid.UUID],
    user_id: uuid.UUID,
    session_id: uuid.UUID,
) -> list[Attachment]:
    """Load the caller's attachments for a turn and bind them to the session.

    Binding on first use is what distinguishes a sent attachment from one that
    was uploaded and abandoned.
    """
    if not attachment_ids:
        return []
    if len(attachment_ids) > settings.UPLOAD_MAX_PER_MESSAGE:
        raise HTTPException(
            422,
            f"At most {settings.UPLOAD_MAX_PER_MESSAGE} files can be attached "
            "to one message",
        )

    attachments: list[Attachment] = []
    for attachment_id in dict.fromkeys(attachment_ids):  # de-dupe, keep order
        attachment = await get_owned(db, attachment_id, user_id)
        if attachment.session_id is None:
            attachment.session_id = session_id
        elif attachment.session_id != session_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "That file was already attached to a different conversation",
            )
        attachments.append(attachment)
    return attachments


async def delete(db: AsyncSession, attachment: Attachment) -> None:
    """Remove the row and its stored bytes."""
    try:
        storage.delete(attachment.storage_key)
    except StorageError as exc:
        # The row still goes; a leftover blob is cheaper than a stuck record.
        logger.warning("Could not delete blob %s: %s", attachment.storage_key, exc)
    await db.delete(attachment)


def describe_for_prompt(attachments: list[Attachment]) -> str:
    """Render document attachments as labelled, citable blocks.

    Only text is returned; images are handled separately as vision content.
    The text is already sanitised at extraction time, and the caller wraps
    this whole block in the untrusted-content template.
    """
    blocks: list[str] = []
    for index, attachment in enumerate(attachments, start=1):
        if attachment.kind is not AttachmentKind.document:
            continue
        label = f"[Attachment {index}: {attachment.filename}]"
        if attachment.extraction_status in (
            ExtractionStatus.failed,
            ExtractionStatus.unsupported,
        ):
            blocks.append(f"{label}\n(This file could not be read.)")
        elif attachment.extraction_status is ExtractionStatus.empty:
            blocks.append(
                f"{label}\n(No text could be extracted. It may be a scanned "
                "document saved as images.)"
            )
        else:
            body = attachment.extracted_text or ""
            if attachment.extraction_status is ExtractionStatus.truncated:
                body += "\n\n(Document truncated: only the first part is shown.)"
            blocks.append(f"{label}\n{body}")
    return "\n\n".join(blocks)
