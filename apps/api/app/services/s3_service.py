"""Centralized S3 asset service.

Stores and serves every user-generated artifact across the platform:
user uploads, generated STL files (InfiniteParts), exportable PDF factory
blueprints (Factorizer), and exam question media (ExamFlow). Public reads are
served through CloudFront (``S3_PUBLIC_BASE_URL``); writes use presigned PUT
URLs so large uploads bypass the API entirely.
"""
from __future__ import annotations

import uuid
from typing import Literal

import boto3
from botocore.config import Config

from app.core.config import settings

AssetKind = Literal["uploads", "stl", "blueprints", "exam_media", "memes"]


class S3Service:
    def __init__(self) -> None:
        self._client = boto3.client(
            "s3",
            region_name=settings.AWS_REGION,
            config=Config(signature_version="s3v4"),
        )
        self.bucket = settings.S3_BUCKET

    def build_key(self, kind: AssetKind, filename: str, owner_id: str | None = None) -> str:
        prefix = f"{kind}/{owner_id}" if owner_id else kind
        return f"{prefix}/{uuid.uuid4().hex}-{filename}"

    def public_url(self, key: str) -> str:
        return f"{settings.S3_PUBLIC_BASE_URL.rstrip('/')}/{key}"

    def presign_upload(
        self, key: str, content_type: str, expires: int = 900
    ) -> dict[str, str]:
        """Presigned PUT URL for direct browser->S3 uploads."""
        url = self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires,
        )
        return {"upload_url": url, "key": key, "public_url": self.public_url(key)}

    def presign_download(self, key: str, expires: int = 900) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires,
        )

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        """Server-side upload (used for generated STL/PDF blobs)."""
        self._client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )
        return self.public_url(key)


s3_service = S3Service()
