"""Builders for real sample files used by the upload tests.

Everything here produces genuine bytes of the format in question, so the
sniffing and extraction paths are exercised for real rather than mocked.
"""
from __future__ import annotations

import io
import struct
import zlib


def png_bytes(width: int = 2, height: int = 2) -> bytes:
    """A valid minimal PNG (solid colour), built without an image library."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    # Each scanline is a filter byte followed by RGB triples.
    raw = b"".join(b"\x00" + b"\xe0\xa0\x34" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def pdf_bytes(text: str = "Mansa Musa ruled the Mali Empire.") -> bytes:
    """A single-page PDF containing ``text``, with a correct xref table."""
    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    stream = f"BT /F1 12 Tf 40 150 Td ({escaped}) Tj ET".encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")

    xref_at = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n".encode()
    )
    return out.getvalue()


def docx_bytes(paragraphs: list[str] | None = None) -> bytes:
    """A real .docx built with python-docx."""
    import docx

    document = docx.Document()
    for line in paragraphs or ["Queen Amina of Zazzau led the Hausa cavalry."]:
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def zip_bytes() -> bytes:
    """A plain zip: a supported-looking container that must still be rejected."""
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("payload.txt", "not a document")
    return buffer.getvalue()
