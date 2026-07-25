"""Shared helpers for bounded file uploads.

``read_capped`` streams an UploadFile in chunks and aborts as soon as the size
limit is exceeded, so a hostile client can't force the whole (arbitrarily large)
body into RAM before the limit check runs. It also honours the Content-Length
header for an early rejection when the client is honest about the size.
"""
import os

from fastapi import HTTPException, Request, UploadFile

from app.core.config import get_settings

_CHUNK = 1024 * 1024  # 1 MiB
# app/ package root — a relative UPLOAD_DIR resolves under this (matching the
# config comment), so uploads land in the same place regardless of the CWD the
# app is started from.
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def upload_root(*subdirs: str) -> str:
    """Absolute upload directory (optionally a subdir), created if missing.
    An absolute UPLOAD_DIR is used as-is; a relative one resolves under app/."""
    base = get_settings().UPLOAD_DIR
    if not os.path.isabs(base):
        base = os.path.join(_APP_ROOT, base)
    d = os.path.join(base, *subdirs) if subdirs else base
    os.makedirs(d, exist_ok=True)
    return d


async def read_capped(file: UploadFile, max_bytes: int, request: Request = None) -> bytes:
    # Early rejection when the client declares an oversize body.
    if request is not None:
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > max_bytes + _CHUNK:
            raise HTTPException(status_code=413, detail="File is too large")

    buf = bytearray()
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise HTTPException(status_code=413, detail="File is too large")
    return bytes(buf)
