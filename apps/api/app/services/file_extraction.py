"""Turn an uploaded file into text the model can read.

The declared ``Content-Type`` is a client-supplied claim, so the real type is
sniffed from the file's own bytes and everything else is rejected. Anything
extracted here is untrusted content — a PDF can contain "ignore your previous
instructions" as easily as a user can type it — so the text goes through the
same sanitiser as retrieved RAG chunks before any caller sees it.
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import dataclass, field

from app.core.config import settings
from app.models.attachment import AttachmentKind, ExtractionStatus
from app.services.ask_retrieval_policy import sanitize_text

logger = logging.getLogger(__name__)

# Sniffed type -> (canonical content type, kind). Nothing outside this map is
# accepted, so the upload surface stays small and predictable.
PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "application/json"}

# Extensions only used to label plain text, which has no magic number.
_TEXT_EXTENSIONS = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".log": "text/plain",
}

SUPPORTED_DESCRIPTION = "PDF, DOCX, TXT, MD, CSV, JSON, PNG, JPEG, WEBP, GIF"


class UnsupportedFileError(ValueError):
    """The bytes are not one of the accepted types."""


@dataclass
class ExtractionResult:
    content_type: str
    kind: AttachmentKind
    status: ExtractionStatus
    text: str | None = None
    meta: dict = field(default_factory=dict)


def sniff_content_type(data: bytes, filename: str) -> str:
    """Identify a file from its bytes, falling back to extension for text."""
    if data.startswith(b"%PDF-"):
        return PDF
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"PK\x03\x04"):
        # Office formats are zip containers; the part list identifies them.
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                if "word/document.xml" in zf.namelist():
                    return DOCX
        except zipfile.BadZipFile:
            pass
        raise UnsupportedFileError(
            "That looks like a zip archive. Supported types: "
            f"{SUPPORTED_DESCRIPTION}."
        )

    # No magic number: accept it only if it is genuinely decodable text.
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix in _TEXT_EXTENSIONS:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedFileError(
                "That file is not valid UTF-8 text."
            ) from exc
        return _TEXT_EXTENSIONS[suffix]

    raise UnsupportedFileError(
        f"Unsupported file type. Supported types: {SUPPORTED_DESCRIPTION}."
    )


def extract(data: bytes, filename: str) -> ExtractionResult:
    """Sniff, parse, sanitise and cap the text content of an upload."""
    content_type = sniff_content_type(data, filename)

    if content_type in IMAGE_TYPES:
        # Images carry no extractable text; they go to a vision model as-is.
        return ExtractionResult(
            content_type=content_type,
            kind=AttachmentKind.image,
            status=ExtractionStatus.ok,
            meta={"bytes": len(data)},
        )

    try:
        if content_type == PDF:
            raw, meta = _extract_pdf(data)
        elif content_type == DOCX:
            raw, meta = _extract_docx(data)
        else:
            raw, meta = _extract_text(data, content_type)
    except Exception as exc:  # a corrupt file must not 500 the upload
        logger.warning("Extraction failed for %s (%s): %s", filename, content_type, exc)
        return ExtractionResult(
            content_type=content_type,
            kind=AttachmentKind.document,
            status=ExtractionStatus.failed,
            meta={"error": type(exc).__name__},
        )

    # Untrusted content: neutralise injection markers before it goes anywhere.
    text = sanitize_text(raw)

    if not text.strip():
        return ExtractionResult(
            content_type=content_type,
            kind=AttachmentKind.document,
            status=ExtractionStatus.empty,
            meta=meta,
        )

    limit = settings.UPLOAD_EXTRACT_MAX_CHARS
    status = ExtractionStatus.ok
    if len(text) > limit:
        text = text[:limit]
        status = ExtractionStatus.truncated
        meta = {**meta, "truncated_to_chars": limit}

    return ExtractionResult(
        content_type=content_type,
        kind=AttachmentKind.document,
        status=status,
        text=text,
        meta=meta,
    )


def _extract_pdf(data: bytes) -> tuple[str, dict]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        # An empty-password decrypt covers PDFs that are merely permission
        # locked; a genuinely protected file raises and is reported as failed.
        reader.decrypt("")
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages), {"page_count": len(pages)}


def _extract_docx(data: bytes) -> tuple[str, dict]:
    import docx

    document = docx.Document(io.BytesIO(data))
    blocks = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))
    return "\n".join(blocks), {"paragraph_count": len(document.paragraphs)}


def _extract_text(data: bytes, content_type: str) -> tuple[str, dict]:
    text = data.decode("utf-8", errors="replace")
    if content_type == "application/json":
        # Pretty-print so the model reads structure rather than one long line.
        try:
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    return text, {"line_count": text.count("\n") + 1}
