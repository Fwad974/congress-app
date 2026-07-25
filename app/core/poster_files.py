"""
Poster image/PDF storage — same opaque-uuid, traversal-safe approach as
``paper_files``, but for poster artwork (images or a PDF). Stored under a
``posters/`` subdirectory of ``UPLOAD_DIR``.
"""
import os
import uuid

from app.core.config import get_settings

ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"}
EXT_LABEL = "image or PDF (.png, .jpg, .jpeg, .webp, .gif, .pdf)"


def ext_of(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def is_allowed(filename: str) -> bool:
    return ext_of(filename) in ALLOWED_EXTS


def max_bytes() -> int:
    return get_settings().MAX_UPLOAD_MB * 1024 * 1024


def _dir() -> str:
    from app.core.uploads import upload_root
    return upload_root("posters")


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
