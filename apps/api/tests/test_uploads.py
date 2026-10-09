"""Chat file upload API: validation, type sniffing, ownership and quota."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.core.config import settings
from tests.files import docx_bytes, pdf_bytes, png_bytes, zip_bytes

UPLOADS = "/api/v1/uploads"


async def _upload(
    client: AsyncClient,
    headers: dict,
    data: bytes,
    filename: str,
    content_type: str = "application/octet-stream",
):
    return await client.post(
        UPLOADS,
        headers=headers,
        files={"file": (filename, data, content_type)},
    )


# --------------------------------------------------------------------------- #
# Happy paths, one per supported family
# --------------------------------------------------------------------------- #
async def test_upload_pdf_extracts_text(client: AsyncClient, auth_headers) -> None:
    headers = await auth_headers()
    resp = await _upload(client, headers, pdf_bytes(), "lesson.pdf", "application/pdf")

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "document"
    assert body["content_type"] == "application/pdf"
    assert body["extraction_status"] == "ok"
    assert "Mansa Musa" in body["text_preview"]
    assert body["meta"]["page_count"] == 1
    assert body["session_id"] is None  # not bound until it is used in a message


async def test_upload_docx_extracts_text(client: AsyncClient, auth_headers) -> None:
    headers = await auth_headers()
    resp = await _upload(client, headers, docx_bytes(), "notes.docx")

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "document"
    assert "Queen Amina" in body["text_preview"]


async def test_upload_plain_text(client: AsyncClient, auth_headers) -> None:
    headers = await auth_headers()
    resp = await _upload(client, headers, b"The Benin Bronzes.", "essay.txt", "text/plain")

    assert resp.status_code == 201
    assert resp.json()["text_preview"] == "The Benin Bronzes."


async def test_upload_json_is_pretty_printed(client: AsyncClient, auth_headers) -> None:
    headers = await auth_headers()
    resp = await _upload(client, headers, b'{"a":1,"b":[2,3]}', "data.json")

    assert resp.status_code == 201
    # Re-formatted so the model sees structure, not one long line.
    assert "\n" in resp.json()["text_preview"]


async def test_upload_image_is_marked_for_vision(
    client: AsyncClient, auth_headers
) -> None:
    headers = await auth_headers()
    resp = await _upload(client, headers, png_bytes(), "homework.png", "image/png")

    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "image"
    assert body["content_type"] == "image/png"
    assert body["text_preview"] is None  # images carry no extractable text


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
async def test_type_is_sniffed_not_trusted(client: AsyncClient, auth_headers) -> None:
    """A renamed binary claiming to be a PDF must be rejected."""
    headers = await auth_headers()
    resp = await _upload(
        client, headers, b"MZ\x90\x00binary payload", "invoice.pdf", "application/pdf"
    )
    assert resp.status_code == 415

    # And a real PNG keeps its true type even when mislabelled as a PDF.
    ok = await _upload(client, headers, png_bytes(), "photo.pdf", "application/pdf")
    assert ok.status_code == 201
    assert ok.json()["content_type"] == "image/png"


async def test_unsupported_types_rejected(client: AsyncClient, auth_headers) -> None:
    headers = await auth_headers()
    archive = await _upload(client, headers, zip_bytes(), "bundle.zip")
    assert archive.status_code == 415
    assert "zip" in archive.json()["detail"].lower()

    binary = await _upload(client, headers, b"\x00\x01\x02\x03", "thing.bin")
    assert binary.status_code == 415


async def test_empty_and_oversized_files_rejected(
    client: AsyncClient, auth_headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await auth_headers()
    empty = await _upload(client, headers, b"", "nothing.txt")
    assert empty.status_code == 422

    monkeypatch.setattr(settings, "UPLOAD_MAX_BYTES", 64)
    big = await _upload(client, headers, b"x" * 200, "big.txt")
    assert big.status_code == 413


async def test_quota_is_enforced(
    client: AsyncClient, auth_headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await auth_headers()
    monkeypatch.setattr(settings, "UPLOAD_USER_QUOTA_BYTES", 100)

    first = await _upload(client, headers, b"a" * 60, "one.txt")
    assert first.status_code == 201
    second = await _upload(client, headers, b"b" * 60, "two.txt")
    assert second.status_code == 413
    assert "storage limit" in second.json()["detail"]


async def test_filename_is_sanitised(client: AsyncClient, auth_headers) -> None:
    """Directory components in the name must never survive."""
    headers = await auth_headers()
    resp = await _upload(client, headers, b"hello", "../../../etc/passwd.txt")

    assert resp.status_code == 201
    assert resp.json()["filename"] == "passwd.txt"


# --------------------------------------------------------------------------- #
# Retrieval, ownership and deletion
# --------------------------------------------------------------------------- #
async def test_download_returns_the_original_bytes(
    client: AsyncClient, auth_headers
) -> None:
    headers = await auth_headers()
    original = png_bytes()
    upload_id = (await _upload(client, headers, original, "pic.png")).json()["id"]

    resp = await client.get(f"{UPLOADS}/{upload_id}/content", headers=headers)
    assert resp.status_code == 200
    assert resp.content == original
    assert resp.headers["content-type"].startswith("image/png")
    # Never render user content inline on our own origin.
    assert resp.headers["content-disposition"].startswith("attachment")
    assert resp.headers["x-content-type-options"] == "nosniff"


async def test_uploads_are_private_to_their_owner(
    client: AsyncClient, auth_headers
) -> None:
    owner = await auth_headers()
    intruder = await auth_headers()
    upload_id = (await _upload(client, owner, b"secret notes", "mine.txt")).json()["id"]

    for path in (f"{UPLOADS}/{upload_id}", f"{UPLOADS}/{upload_id}/content"):
        assert (await client.get(path, headers=intruder)).status_code == 403
    assert (await client.delete(f"{UPLOADS}/{upload_id}", headers=intruder)).status_code == 403

    # The listing is scoped per account too.
    assert (await client.get(UPLOADS, headers=intruder)).json() == []
    assert len((await client.get(UPLOADS, headers=owner)).json()) == 1


async def test_endpoints_require_authentication(client: AsyncClient) -> None:
    ghost = uuid.uuid4()
    assert (await client.get(UPLOADS)).status_code == 401
    assert (await client.get(f"{UPLOADS}/limits")).status_code == 401
    assert (await client.get(f"{UPLOADS}/{ghost}")).status_code == 401
    assert (await client.delete(f"{UPLOADS}/{ghost}")).status_code == 401
    resp = await client.post(UPLOADS, files={"file": ("a.txt", b"hi", "text/plain")})
    assert resp.status_code == 401


async def test_unknown_upload_is_404(client: AsyncClient, auth_headers) -> None:
    headers = await auth_headers()
    assert (await client.get(f"{UPLOADS}/{uuid.uuid4()}", headers=headers)).status_code == 404


async def test_delete_removes_file_and_bytes(client: AsyncClient, auth_headers) -> None:
    headers = await auth_headers()
    upload_id = (await _upload(client, headers, b"temporary", "temp.txt")).json()["id"]

    assert (await client.delete(f"{UPLOADS}/{upload_id}", headers=headers)).status_code == 204
    assert (await client.get(f"{UPLOADS}/{upload_id}", headers=headers)).status_code == 404


async def test_limits_endpoint_reports_usage(client: AsyncClient, auth_headers) -> None:
    headers = await auth_headers()
    before = (await client.get(f"{UPLOADS}/limits", headers=headers)).json()
    assert before["used_bytes"] == 0
    assert before["max_bytes"] == settings.UPLOAD_MAX_BYTES
    assert "application/pdf" in before["accepted_types"]
    assert "image/png" in before["accepted_types"]

    await _upload(client, headers, b"x" * 42, "note.txt")
    after = (await client.get(f"{UPLOADS}/limits", headers=headers)).json()
    assert after["used_bytes"] == 42
