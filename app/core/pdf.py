"""
Server-side certificate PDF generation (reportlab).

Produces a landscape A4 certificate matching the look of the printable HTML
version (cream card, double gold rule, serif type), plus what the HTML can't
carry: an embedded QR code pointing at the public verification page and the
certificate serial, so the downloaded file is a self-contained, checkable
credential.
"""
import io

import segno
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

GOLD = HexColor("#b49a5a")
GREEN = HexColor("#0a3d2e")
CREAM = HexColor("#fffdf7")
BODY = HexColor("#444444")
MUTED = HexColor("#8a7a4e")


def _qr_png(data: str, scale: int = 6) -> io.BytesIO:
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="png", scale=scale,
                                     dark="#0a3d2e", light="#ffffff")
    buf.seek(0)
    return buf


def _wrap(text: str, font: str, size: float, max_width: float) -> list:
    words, lines, line = text.split(), [], ""
    for w in words:
        candidate = f"{line} {w}".strip()
        if stringWidth(candidate, font, size) <= max_width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def certificate_pdf(*, holder: str, cert_title: str, body_text: str,
                    issued_on: str, serial: str, verify_url: str,
                    congress_full: str, congress_short: str) -> bytes:
    """Render the certificate and return raw PDF bytes."""
    buf = io.BytesIO()
    w, h = landscape(A4)  # 842 x 595 pt
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setTitle(f"{cert_title} — {congress_full}")

    # Card background + double gold border.
    c.setFillColor(CREAM)
    c.rect(0, 0, w, h, stroke=0, fill=1)
    c.setStrokeColor(GOLD)
    c.setLineWidth(3)
    c.rect(28, 28, w - 56, h - 56)
    c.setLineWidth(1)
    c.rect(38, 38, w - 76, h - 76)

    center = w / 2

    # Congress name (letter-spaced small caps look).
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 11)
    spaced = "  ".join(congress_full.upper())
    c.drawCentredString(center, h - 96, spaced)

    # Certificate title + divider.
    c.setFillColor(GREEN)
    c.setFont("Times-Bold", 32)
    c.drawCentredString(center, h - 148, cert_title)
    c.setStrokeColor(GOLD)
    c.setLineWidth(2)
    c.line(center - 60, h - 168, center + 60, h - 168)

    # Presented to + holder name.
    c.setFillColor(BODY)
    c.setFont("Times-Italic", 13)
    c.drawCentredString(center, h - 198, "This certificate is proudly presented to")
    c.setFillColor(GREEN)
    c.setFont("Times-Bold", 26)
    c.drawCentredString(center, h - 236, holder)

    # Body paragraph, wrapped and centred.
    c.setFillColor(BODY)
    c.setFont("Times-Roman", 13)
    y = h - 274
    for line in _wrap(body_text, "Times-Roman", 13, 460):
        c.drawCentredString(center, y, line)
        y -= 19

    # Verification QR (bottom right) with serial + URL caption.
    qr_size = 92
    qr_x, qr_y = w - 76 - qr_size, 78
    c.drawImage(ImageReader(_qr_png(verify_url)), qr_x, qr_y,
                width=qr_size, height=qr_size)
    c.setFont("Helvetica", 7.5)
    c.setFillColor(MUTED)
    c.drawCentredString(qr_x + qr_size / 2, qr_y - 12, "Scan to verify")
    c.drawCentredString(qr_x + qr_size / 2, qr_y - 22, serial)

    # Signature block: date issued + organizing committee.
    c.setStrokeColor(HexColor("#999999"))
    c.setLineWidth(0.8)
    c.setFillColor(BODY)
    for x, label, value in ((110, "Date issued", issued_on),
                            (350, "Organizing Committee", congress_short)):
        c.line(x, 106, x + 190, 106)
        c.setFont("Helvetica", 8.5)
        c.drawString(x, 94, label)
        c.setFont("Helvetica-Bold", 10.5)
        c.drawString(x, 80, value)

    # Tiny verification footer, useful when the QR can't be scanned.
    c.setFont("Helvetica", 7)
    c.setFillColor(MUTED)
    c.drawCentredString(center, 48, f"Verify authenticity: {verify_url}")

    c.showPage()
    c.save()
    return buf.getvalue()
