"""Local-filesystem storage for event documents.

Phase 2 of `docs/EVENT_DETAIL_TABS_PHASES.md`. Kept narrow on purpose so a B2/S3
implementation can sit behind the same surface later (`put_object`,
`open_object`, `delete_object`, `resolve_path`) without rewriting the routers.

Storage keys are `events/{event_id}/{document_id}/{slugified_filename}` — see
`build_key`. The slug guarantees the on-disk filename is safe regardless of
what the customer uploaded.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import BinaryIO

from config.settings import DOCUMENT_STORAGE_ROOT

_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_DASH_RUN = re.compile(r"-+")
_CHUNK = 64 * 1024


def slugify_filename(name: str) -> str:
    """Reduce a user-supplied filename to `[A-Za-z0-9._-]+`. Empty -> 'file'."""
    name = (name or "").strip().lower()
    name = _SLUG_UNSAFE.sub("-", name)
    name = _DASH_RUN.sub("-", name).strip("-.")
    return name or "file"


def build_key(event_id: int, document_id: int, filename: str) -> str:
    return f"events/{event_id}/{document_id}/{slugify_filename(filename)}"


def _root() -> Path:
    return Path(DOCUMENT_STORAGE_ROOT).resolve()


def resolve_path(key: str) -> Path:
    """Resolve a storage key to an absolute path inside DOCUMENT_STORAGE_ROOT.

    Rejects absolute keys, `..` traversal, and symlink escapes. The check uses
    `is_relative_to` against the resolved root so a symlinked subdir would have
    to point inside the root to pass.
    """
    if not key or key.startswith("/") or key.startswith("\\"):
        raise ValueError(f"invalid storage key: {key!r}")
    root = _root()
    candidate = (root / key).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"path traversal rejected: {key!r}")
    return candidate


def put_object(key: str, fileobj: BinaryIO) -> int:
    """Stream `fileobj` to disk at `key`. Returns total bytes written.

    Caller is expected to have already validated size and content type.
    """
    path = resolve_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("wb") as out:
        while True:
            chunk = fileobj.read(_CHUNK)
            if not chunk:
                break
            out.write(chunk)
            written += len(chunk)
    return written


def open_object(key: str) -> BinaryIO:
    """Open the stored object for reading. Raises FileNotFoundError if missing."""
    return resolve_path(key).open("rb")


def object_exists(key: str) -> bool:
    try:
        return resolve_path(key).is_file()
    except ValueError:
        return False


def delete_object(key: str) -> None:
    """Best-effort delete. No-op if the file is already gone."""
    try:
        path = resolve_path(key)
    except ValueError:
        return
    if path.exists():
        path.unlink()


def free_bytes() -> int:
    """Bytes free on the filesystem holding DOCUMENT_STORAGE_ROOT.

    Used by the Phase 5 disk-space guard. Creates the root if it doesn't yet
    exist so we can call this on a fresh deploy.
    """
    return disk_usage().free


def disk_usage() -> shutil._ntuple_diskusage:
    """Total/used/free triple for the upload filesystem. Mkdirs the root."""
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(root)
