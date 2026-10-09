"""Chat file uploads (``/api/v1/uploads``).

Files are uploaded on their own and referenced by id when a message is sent,
so the composer can show a preview and a progress bar before the user commits
to a question. Every route is owner-scoped: an attachment is only ever visible
to the account that uploaded it.
"""
from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import rate_limited
from app.deps import get_current_user
from app.models.attachment import Attachment
from app.models.user import User
from app.schemas.uploads import AttachmentDetail, AttachmentRead, UploadLimits
from app.services import attachment_service, file_extraction
from app.services.storage_service import StorageError, storage

router = APIRouter(prefix="/uploads", tags=["uploads"])

_PREVIEW_CHARS = 600


@router.get("/limits", response_model=UploadLimits, summary="Upload rules and usage")
async def limits(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UploadLimits:
    """What the composer may send, and how much of the quota is already used."""
    return UploadLimits(
        max_bytes=settings.UPLOAD_MAX_BYTES,
        max_files_per_message=settings.UPLOAD_MAX_PER_MESSAGE,
        quota_bytes=settings.UPLOAD_USER_QUOTA_BYTES,
        used_bytes=await attachment_service.used_bytes(db, user.id),
        accepted_types=sorted(
            {
                file_extraction.PDF,
                file_extraction.DOCX,
                *file_extraction.TEXT_TYPES,
                *file_extraction.IMAGE_TYPES,
            }
        ),
    )


@router.post(
    "",
    response_model=AttachmentDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limited(30))],
    summary="Upload a file for use in chat",
)
async def upload(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AttachmentDetail:
    """Accept one file, extract its text, and return a referenceable id.

    The real type is detected from the file's bytes, so renaming a `.exe` to
    `.pdf` is rejected. Pass the returned `id` in `attachment_ids` on
    `POST /ask` to put the file in front of the model.
    """
    data = await file.read()
    attachment = await attachment_service.create(
        db, user_id=user.id, data=data, filename=file.filename
    )
    return _to_detail(attachment)


@router.get("", response_model=list[AttachmentRead], summary="List your uploads")
async def list_uploads(
    session_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[AttachmentRead]:
    """The caller's uploads, newest first; optionally scoped to one session."""
    stmt = select(Attachment).where(Attachment.user_id == user.id)
    if session_id is not None:
        stmt = stmt.where(Attachment.session_id == session_id)
    stmt = stmt.order_by(Attachment.created_at.desc())
    rows = (await db.execute(stmt)).scalars().all()
    return [AttachmentRead.model_validate(row) for row in rows]


@router.get("/{attachment_id}", response_model=AttachmentDetail, summary="Upload metadata")
async def get_upload(
    attachment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AttachmentDetail:
    attachment = await attachment_service.get_owned(db, attachment_id, user.id)
    return _to_detail(attachment)


@router.get(
    "/{attachment_id}/content",
    summary="Download the original file",
    response_class=Response,
)
async def download(
    attachment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Return the stored bytes, for thumbnails and re-download.

    Browsers cannot put an `Authorization` header on an `<img src>`, so fetch
    this with the token and render the result through `URL.createObjectURL`.
    """
    attachment = await attachment_service.get_owned(db, attachment_id, user.id)
    try:
        data = storage.load(attachment.storage_key)
    except StorageError:
        return Response(status_code=status.HTTP_410_GONE)

    # Always an attachment-style disposition with a quoted, encoded filename:
    # the file is user-supplied, so it must never render inline on our origin.
    return Response(
        content=data,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(attachment.filename)}"
            ),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.delete(
    "/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an upload",
)
async def delete_upload(
    attachment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Remove the file and its stored bytes. Messages keep their text record."""
    attachment = await attachment_service.get_owned(db, attachment_id, user.id)
    await attachment_service.delete(db, attachment)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _to_detail(attachment: Attachment) -> AttachmentDetail:
    preview = (attachment.extracted_text or "")[:_PREVIEW_CHARS] or None
    detail = AttachmentDetail.model_validate(attachment)
    detail.text_preview = preview
    detail.meta = attachment.meta or {}
    return detail
