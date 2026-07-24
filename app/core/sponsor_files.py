"""
Sponsor logo storage — same opaque-uuid, traversal-safe approach as
``poster_files``, scoped to a ``sponsors/`` subdirectory of ``UPLOAD_DIR``.

Raster images only (no SVG): an SVG served inline can carry script, so we keep
logos to safe raster formats.
"""
import os
import uuid

from app.core.config import get_settings

ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
EXT_LABEL = "image (.png, .jpg, .jpeg, .webp, .gif)"
_CONTENT_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}


def ext_of(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def is_allowed(filename: str) -> bool:
    return ext_of(filename) in ALLOWED_EXTS


def content_type(stored: str) -> str:
    return _CONTENT_TYPES.get(ext_of(stored), "application/octet-stream")


def max_bytes() -> int:
    return get_settings().MAX_UPLOAD_MB * 1024 * 1024


def _dir() -> str:
    d = os.path.join(get_settings().UPLOAD_DIR, "sponsors")
    os.makedirs(d, exist_ok=True)
    return d


def save_bytes(data: bytes, original_name: str) -> str:
    stored = f"{uuid.uuid4().hex}{ext_of(original_name)}"
    with open(os.path.join(_dir(), stored), "wb") as fh:
        fh.write(data)
    return stored


def path_for(stored: str) -> str:
    base = os.path.abspath(_dir())
    full = os.path.abspath(os.path.join(base, os.path.basename(stored or "")))
    if os.path.commonpath([base, full]) != base:
        raise ValueError("Invalid stored file path")
    return full


def delete_file(stored: str) -> None:
    if not stored:
        return
    try:
        os.remove(path_for(stored))
    except (FileNotFoundError, ValueError):
        pass
