"""
QR-code generation (segno — pure Python, no native deps).

Used for the poster scavenger hunt and session attendance check-in: the QR
encodes an app URL with a `checkin` query param, so scanning it with any phone
camera opens the app and records the check-in.
"""
import io

import segno


def qr_svg_document(data: str, scale: int = 8) -> str:
    """Full SVG XML for ``data``, suitable to serve as image/svg+xml."""
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=scale,
                                     dark="#0a3d2e", light="#ffffff")
    return buf.getvalue().decode("utf-8")
