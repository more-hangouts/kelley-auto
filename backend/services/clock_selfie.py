"""Selfie storage for clock-in punches (Phase 7 Slice 2 of the Sales Portal).

Tight backend limits — the deployment failure mode for selfie writes
is the systemd `ReadWritePaths` line not covering
`/var/lib/bellas-xv/uploads`. When that's missing, the disk write
raises `PermissionError` / `OSError` and we surface a stable
`selfie_storage_unavailable` 503 with a clear code so the failure
shows up as an obvious server error at the browser, not a generic
500 that looks like a CORS problem.

Pipeline:

  1. Reject by claimed mime type (allowlist).
  2. Reject if oversize (1 MB strict cap).
  3. Open with Pillow — verifies the bytes really are an image, not
     just a renamed payload, and rejects truncated / malformed files.
  4. Cap dimensions at 1024x1024 (preserves aspect, downsamples).
  5. Re-encode to WebP at quality=80. EXIF and ICC profile are NOT
     carried over because Pillow's WebP encoder only includes them
     when explicitly passed, so we get the strip for free.
  6. Atomically `put_object` under `clockin/{user_id}/{punch_id}.webp`.

A selfie endpoint must call this at most once per punch. The output
storage key is what gets persisted on `staff_punches.selfie_storage_key`.
"""

from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError

from services import document_storage

SELFIE_MAX_BYTES = 1_000_000  # 1 MB strict
SELFIE_MIN_BYTES = 200  # absurd-small guard; a real WebP is ~kB minimum
ALLOWED_INPUT_MIME = frozenset({"image/webp", "image/jpeg", "image/png"})
ALLOWED_PILLOW_FORMATS = frozenset({"WEBP", "JPEG", "PNG"})
MAX_DIMENSION = 1024
WEBP_QUALITY = 80


class SelfieStorageError(Exception):
    """Stable error codes the router maps to HTTP statuses.

    Codes:
        selfie_unsupported_type    415 — claimed mime not in allowlist
        selfie_too_large           413 — over SELFIE_MAX_BYTES
        selfie_invalid             400 — Pillow couldn't decode it,
                                         or bytes were absurdly small
        selfie_storage_unavailable 503 — disk write failed (most
                                         likely the systemd
                                         ReadWritePaths line is missing
                                         /var/lib/bellas-xv/uploads)
    """

    def __init__(self, code: str, *, http_status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def selfie_storage_key(*, user_id: int, punch_id: int) -> str:
    return f"clockin/{user_id}/{punch_id}.webp"


def validate_selfie_bytes(
    *, raw_bytes: bytes, declared_mime: str | None
) -> bytes:
    """Validate input + re-encode to bounded WebP. Returns the WebP bytes
    ready for `write_selfie_bytes`. No disk I/O.

    Split from the disk write so the router can validate before the
    punch row is created. A 4xx for a bad selfie should not waste a
    punch_id, and the selfie is what most often gets rejected (size
    limit, weird image format, truncated upload).
    """
    if declared_mime is None or declared_mime.lower() not in ALLOWED_INPUT_MIME:
        raise SelfieStorageError(
            "selfie_unsupported_type", http_status=415
        )
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise SelfieStorageError("selfie_invalid", http_status=400)
    if len(raw_bytes) > SELFIE_MAX_BYTES:
        raise SelfieStorageError("selfie_too_large", http_status=413)
    if len(raw_bytes) < SELFIE_MIN_BYTES:
        raise SelfieStorageError("selfie_invalid", http_status=400)
    return _normalize_to_webp(raw_bytes)


def write_selfie_bytes(
    *, user_id: int, punch_id: int, webp_bytes: bytes
) -> str:
    """Persist pre-validated WebP bytes. Returns the storage key.

    Raises `SelfieStorageError('selfie_storage_unavailable', 503)` if
    the disk write fails — the failure path the deploy gate guards
    against.
    """
    key = selfie_storage_key(user_id=user_id, punch_id=punch_id)
    try:
        document_storage.put_object(key, io.BytesIO(webp_bytes))
    except (PermissionError, OSError) as exc:
        # Disk write failed. In production this is almost always the
        # systemd unit's `ReadWritePaths` line not covering
        # `/var/lib/bellas-xv/uploads`. Surface a stable code so the
        # browser sees a real 503 and not a generic 500 that looks
        # like CORS at the network tab.
        raise SelfieStorageError(
            "selfie_storage_unavailable", http_status=503
        ) from exc
    return key


def store_selfie(
    *,
    user_id: int,
    punch_id: int,
    raw_bytes: bytes,
    declared_mime: str | None,
) -> str:
    """Convenience wrapper: validate + write in one call. The router
    splits these two steps so a validation failure does not waste a
    punch_id; this helper exists for callers that already have the
    punch row and just need to attach a selfie."""
    webp = validate_selfie_bytes(
        raw_bytes=raw_bytes, declared_mime=declared_mime
    )
    return write_selfie_bytes(
        user_id=user_id, punch_id=punch_id, webp_bytes=webp
    )


def _normalize_to_webp(raw_bytes: bytes) -> bytes:
    """Decode → bound dimensions → re-encode WebP without EXIF/ICC."""
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            if img.format not in ALLOWED_PILLOW_FORMATS:
                raise SelfieStorageError(
                    "selfie_invalid", http_status=400
                )
            # Verify the file actually decodes — `Image.open` is lazy.
            img.load()
            # PNGs and weird CMYK JPEGs need an explicit conversion to
            # RGB before WebP encode; alpha is dropped (selfies don't
            # need transparency).
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((MAX_DIMENSION, MAX_DIMENSION))
            buf = io.BytesIO()
            # Default save: no EXIF, no ICC profile — Pillow only
            # writes them when explicitly passed via `save(..., exif=...,
            # icc_profile=...)`. We don't pass either, so the strip is
            # automatic.
            img.save(buf, format="WEBP", quality=WEBP_QUALITY, method=4)
            return buf.getvalue()
    except SelfieStorageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise SelfieStorageError("selfie_invalid", http_status=400) from exc
