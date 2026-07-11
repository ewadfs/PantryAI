"""Storage service — Cloudflare R2 (S3-compatible) image upload / download.

R2 is addressed with boto3 using a custom ``endpoint_url``. The bucket is
private: we never presign or expose public URLs. Images are pulled back as raw
bytes (for base64-ing to Claude Vision) via :func:`get_image_bytes`.

Endpoint derivation: ``settings.r2_account_id`` in this project holds the full
account URL (e.g. ``https://<hash>.r2.cloudflarestorage.com/pantryai-images``),
not a bare account id. :func:`_endpoint` normalizes either form to the bare
``https://<host>`` endpoint boto3 expects (the bucket is addressed separately).
"""

import asyncio
from urllib.parse import urlsplit

import boto3
from botocore.config import Config

from app.config import settings

_client = None


def _endpoint() -> str:
    """Return the R2 S3 endpoint URL (scheme + host, no bucket path)."""
    raw = settings.r2_account_id.strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        parts = urlsplit(raw)
        return f"{parts.scheme}://{parts.netloc}"
    return f"https://{raw}.r2.cloudflarestorage.com"


def _get_client():
    """Lazily construct (and cache) the boto3 S3 client for R2."""
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=_endpoint(),
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )
    return _client


async def upload_image(
    file_bytes: bytes, key: str, content_type: str = "image/jpeg"
) -> str:
    """Store ``file_bytes`` at ``key`` in the private R2 bucket; return ``key``.

    boto3 is synchronous, so the blocking call runs in a worker thread to keep
    the event loop free.
    """

    def _put() -> None:
        _get_client().put_object(
            Bucket=settings.r2_bucket,
            Key=key,
            Body=file_bytes,
            ContentType=content_type,
        )

    await asyncio.to_thread(_put)
    return key


async def get_image_bytes(key: str) -> bytes:
    """Fetch the object at ``key`` from R2 and return its raw bytes."""

    def _get() -> bytes:
        resp = _get_client().get_object(Bucket=settings.r2_bucket, Key=key)
        return resp["Body"].read()

    return await asyncio.to_thread(_get)
