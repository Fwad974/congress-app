"""Shared helpers for bounded file uploads.

``read_capped`` streams an UploadFile in chunks and aborts as soon as the size
limit is exceeded, so a hostile client can't force the whole (arbitrarily large)
body into RAM before the limit check runs. It also honours the Content-Length
header for an early rejection when the client is honest about the size.
"""
from fastapi import HTTPException, Request, UploadFile

_CHUNK = 1024 * 1024  # 1 MiB


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
