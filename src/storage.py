"""
storage.py
==========
Handles Backblaze B2 (S3-compatible) storage for WellRing call recordings.

Responsibility boundary:
  - Backblaze B2  → audio files (call recordings)
  - PostgreSQL     → everything else (users, assessments, health history,
                      health_history, conversations, alerts)

The `assessments.recording_url` column in Postgres stores ONLY the permanent
B2 object key URL — never the raw transient Bolna link.
"""

import asyncio
import logging
import os
import uuid

import httpx
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Env-var accessors (lazy — read at call time so dotenv always takes effect)
# ---------------------------------------------------------------------------

def _cfg() -> dict:
    """Read B2 / S3 config from environment at call time."""
    return {
        "key_id":       os.environ.get("AWS_ACCESS_KEY_ID", ""),
        "app_key":      os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        "region":       os.environ.get("AWS_REGION", "us-east-005"),
        "bucket":       os.environ.get("AWS_BUCKET_NAME", "wellring-recordings"),
        "endpoint_url": os.environ.get("AWS_ENDPOINT_URL", "https://s3.us-east-005.backblazeb2.com"),
    }


def get_s3_client():
    """Return a configured boto3 S3 client for Backblaze B2, or None if not configured."""
    cfg = _cfg()
    if not cfg["key_id"] or not cfg["app_key"]:
        return None, cfg
    client = boto3.client(
        "s3",
        endpoint_url=cfg["endpoint_url"],
        aws_access_key_id=cfg["key_id"],
        aws_secret_access_key=cfg["app_key"],
        region_name=cfg["region"],
        config=Config(signature_version="s3v4"),
    )
    return client, cfg


def is_storage_configured() -> bool:
    """Return True if Backblaze B2 credentials are set in the environment."""
    cfg = _cfg()
    return bool(cfg["key_id"] and cfg["app_key"] and cfg["bucket"])


# ---------------------------------------------------------------------------
# Core upload function
# ---------------------------------------------------------------------------

async def upload_recording_to_b2(original_url: str, phone: str = "unknown") -> str:
    """
    Download a call recording from `original_url` (transient Bolna link) and
    upload it permanently to Backblaze B2.

    Returns:
        Permanent B2 object URL (stored in assessments.recording_url in Postgres).
        Falls back to `original_url` on any error so the call is never lost.

    Architecture:
        Bolna transient URL  →  download  →  B2 bucket  →  permanent URL
                                                          → Postgres (assessments.recording_url)
    """
    if not original_url:
        return ""

    s3_client, cfg = get_s3_client()
    if not s3_client:
        logger.warning("[B2] Credentials not configured — keeping Bolna URL as fallback.")
        return original_url

    try:
        # 1. Download audio from Bolna
        async with httpx.AsyncClient(timeout=60) as http:
            resp = await http.get(original_url)
        if resp.status_code != 200:
            logger.error(f"[B2] Failed to download recording ({resp.status_code}): {original_url}")
            return original_url

        file_content = resp.content
        content_type = resp.headers.get("Content-Type", "audio/mpeg")

        # Pick file extension from content type
        if "wav" in content_type:
            ext = ".wav"
        elif "ogg" in content_type:
            ext = ".ogg"
        elif "webm" in content_type:
            ext = ".webm"
        else:
            ext = ".mp3"

        # 2. Build object key:  recordings/<phone>/<date>/<uuid>.<ext>
        import datetime
        safe_phone = str(phone).strip().replace("+", "").replace(" ", "")
        date_str = datetime.datetime.now().strftime("%Y/%m/%d")
        file_key = f"recordings/{safe_phone}/{date_str}/{uuid.uuid4().hex}{ext}"

        # 3. Upload (run in thread — boto3 is synchronous)
        await asyncio.to_thread(
            s3_client.put_object,
            Bucket=cfg["bucket"],
            Key=file_key,
            Body=file_content,
            ContentType=content_type,
        )

        # 4. Build permanent URL: <endpoint>/<bucket>/<key>
        permanent_url = f"{cfg['endpoint_url'].rstrip('/')}/{cfg['bucket']}/{file_key}"
        logger.info(f"[B2] Recording uploaded: {permanent_url}")
        return permanent_url

    except Exception as exc:
        logger.error(f"[B2] Upload failed: {exc} — falling back to original URL.")
        return original_url


# ---------------------------------------------------------------------------
# Signed URL (for secure retrieval via the API)
# ---------------------------------------------------------------------------

def get_presigned_url(object_url: str, expires_in: int = 3600) -> str:
    """
    Generate a temporary pre-signed URL for a B2 object.

    Args:
        object_url:  The permanent B2 URL stored in Postgres (assessments.recording_url).
        expires_in:  Seconds until the link expires (default 1 hour).

    Returns:
        A time-limited pre-signed URL that can be shared directly with clients.
        Falls back to `object_url` if signing fails.
    """
    s3_client, cfg = get_s3_client()
    if not s3_client or not object_url:
        return object_url

    try:
        # Extract the object key from the full URL
        prefix = f"{cfg['endpoint_url'].rstrip('/')}/{cfg['bucket']}/"
        if not object_url.startswith(prefix):
            return object_url
        key = object_url[len(prefix):]

        presigned = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": cfg["bucket"], "Key": key},
            ExpiresIn=expires_in,
        )
        return presigned
    except ClientError as exc:
        logger.error(f"[B2] Pre-sign failed: {exc}")
        return object_url


# ---------------------------------------------------------------------------
# Back-compat alias (used in main.py)
# ---------------------------------------------------------------------------

async def upload_recording_to_s3(original_url: str, phone: str = "unknown") -> str:
    """Alias of upload_recording_to_b2 for backward compatibility."""
    return await upload_recording_to_b2(original_url, phone)
