"""
Manuscript file storage for paper submissions.

Files are saved under ``settings.UPLOAD_DIR`` with an opaque uuid-based name so
the original (attacker-controlled) filename never touches the filesystem — this
sidesteps path traversal and name collisions. The original name is kept in the
DB (``Paper.file_name``) purely for display and download. Files are served only
through an authenticated endpoint, never from ``/static``.
"""
import os
import uuid

from app.core.config import get_settings

# Manuscript formats we accept. Keep in sync with the accept="" attr in the UI.
ALLOWED_EXTS = {".pdf", ".doc", ".docx"}
EXT_LABEL = "PDF or Word (.pdf, .doc, .docx)"


def ext_of(filename: str) -> str:
    """Lowercase extension incl. dot, e.g. '.pdf' ('' if none)."""
    return os.path.splitext(filename or "")[1].lower()


def is_allowed(filename: str) -> bool:
    return ext_of(filename) in ALLOWED_EXTS


def max_bytes() -> int:
    return get_settings().MAX_UPLOAD_MB * 1024 * 1024


def _upload_dir() -> str:
    from app.core.uploads import upload_root
    return upload_root()


def save_bytes(data: bytes, original_name: str) -> str:
    """Persist ``data`` under a fresh uuid name; return the stored filename."""
    stored = f"{uuid.uuid4().hex}{ext_of(original_name)}"
    with open(os.path.join(_upload_dir(), stored), "wb") as fh:
        fh.write(data)
    return stored


def path_for(stored_file: str) -> str:
    """Absolute path for a stored file, guarding against traversal."""
    d = os.path.abspath(_upload_dir())
    # stored names are uuid+ext, but resolve + contain anyway.
    full = os.path.abspath(os.path.join(d, os.path.basename(stored_file or "")))
    if os.path.commonpath([d, full]) != d:
        raise ValueError("Invalid stored file path")
    return full


def delete_file(stored_file: str) -> None:
    if not stored_file:
        return
    try:
        os.remove(path_for(stored_file))
    except (FileNotFoundError, ValueError):
        pass
