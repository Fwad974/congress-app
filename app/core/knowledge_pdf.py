"""
PDF export of an attendee's personal knowledge map (reportlab).

Portrait A4, paginated: the topics the attendee follows with the sessions,
papers and posters on each, then the cross-pollination suggestions and the
congress's trending topics.
"""
import io
from typing import Dict, List

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

GOLD = HexColor("#b49a5a")
GREEN = HexColor("#0a3d2e")
BODY = HexColor("#333333")
MUTED = HexColor("#7a7a7a")

PAGE_W, PAGE_H = A4
MARGIN = 54
BOTTOM = 64


def _wrap(text: str, font: str, size: float, max_width: float) -> List[str]:
    words, lines, line = (text or "").split(), [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if stringWidth(candidate, font, size) <= max_width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines or [""]


class _Doc:
    """Tiny paginating text cursor — reportlab's canvas has no flow model."""

    def __init__(self, c: canvas.Canvas, title: str, subtitle: str):
        self.c = c
        self.title = title
        self.subtitle = subtitle
        self.y = 0.0
        self.page = 0
        self._new_page()

    def _new_page(self):
        if self.page:
            self.c.showPage()
        self.page += 1
        self.c.setFillColor(GREEN)
        self.c.rect(0, PAGE_H - 92, PAGE_W, 92, stroke=0, fill=1)
        self.c.setFillColor(HexColor("#ffffff"))
        self.c.setFont("Times-Bold", 20)
        self.c.drawString(MARGIN, PAGE_H - 52, self.title)
        self.c.setFillColor(GOLD)
        self.c.setFont("Helvetica", 9.5)
        self.c.drawString(MARGIN, PAGE_H - 70, self.subtitle)
        self.c.setFillColor(MUTED)
        self.c.setFont("Helvetica", 7.5)
        self.c.drawRightString(PAGE_W - MARGIN, 34, f"Page {self.page}")
        self.y = PAGE_H - 124

    def space(self, amount: float):
        if self.y - amount < BOTTOM:
            self._new_page()
        self.y -= amount

    def heading(self, text: str):
        self.space(26)
        self.c.setFillColor(GOLD)
        self.c.setFont("Helvetica-Bold", 8.5)
        self.c.drawString(MARGIN, self.y, text.upper())
        self.c.setStrokeColor(HexColor("#e2dcc8"))
        self.c.setLineWidth(0.8)
        self.c.line(MARGIN, self.y - 5, PAGE_W - MARGIN, self.y - 5)
        self.space(8)

    def topic(self, label: str, meta: str):
        self.space(18)
        self.c.setFillColor(GREEN)
        self.c.setFont("Times-Bold", 13)
        self.c.drawString(MARGIN, self.y, label)
        width = stringWidth(label, "Times-Bold", 13)
        self.c.setFillColor(MUTED)
        self.c.setFont("Helvetica", 8)
        self.c.drawString(MARGIN + width + 8, self.y + 1, meta)
        self.space(4)

    def bullet(self, text: str, detail: str = ""):
        for i, line in enumerate(_wrap(text, "Helvetica", 9.5,
                                       PAGE_W - 2 * MARGIN - 18)):
            self.space(12)
            self.c.setFillColor(BODY)
            self.c.setFont("Helvetica", 9.5)
            if i == 0:
                self.c.setFillColor(GOLD)
                self.c.drawString(MARGIN + 4, self.y, "•")
                self.c.setFillColor(BODY)
            self.c.drawString(MARGIN + 18, self.y, line)
        if detail:
            for line in _wrap(detail, "Helvetica-Oblique", 8.5,
                              PAGE_W - 2 * MARGIN - 18):
                self.space(11)
                self.c.setFillColor(MUTED)
                self.c.setFont("Helvetica-Oblique", 8.5)
                self.c.drawString(MARGIN + 18, self.y, line)

    def note(self, text: str):
        for line in _wrap(text, "Helvetica", 9.5, PAGE_W - 2 * MARGIN):
            self.space(13)
            self.c.setFillColor(MUTED)
            self.c.setFont("Helvetica", 9.5)
            self.c.drawString(MARGIN, self.y, line)


def _when(item: Dict) -> str:
    when = item.get("when")
    if not when:
        return ""
    try:
        return when.strftime("%a %d %b, %H:%M")
    except AttributeError:
        return str(when)[:16]


def knowledge_map_pdf(*, holder: str, data: Dict, congress_full: str,
                      congress_dates: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Knowledge map — {holder}")
    doc = _Doc(c, f"Knowledge map · {holder}", f"{congress_full} · {congress_dates}")

    topics = data.get("topics") or []
    if topics:
        doc.heading("Topics you're following")
        for node in topics:
            doc.topic(node["label"], f"{node['count']} item"
                                     f"{'s' if node['count'] != 1 else ''}")
            for item in node.get("items", []):
                detail = " · ".join(filter(None, [
                    item.get("kind", "").title(), item.get("subtitle") or "",
                    _when(item), item.get("location") or ""]))
                doc.bullet(item.get("title", ""), detail)
            doc.space(6)
    else:
        doc.heading("Topics you're following")
        doc.note("Bookmark a few sessions or add research interests to your "
                 "profile and this map fills in automatically.")

    suggestions = data.get("cross_pollination") or []
    if suggestions:
        doc.heading("Cross-pollination — one room over")
        for suggestion in suggestions:
            doc.topic(suggestion["label"], suggestion.get("because", ""))
            for item in suggestion.get("items", []):
                doc.bullet(item.get("title", ""),
                           " · ".join(filter(None, [item.get("kind", "").title(),
                                                    _when(item)])))
            doc.space(6)

    trending = data.get("trending") or []
    if trending:
        doc.heading("Trending across the congress")
        for row in trending:
            doc.bullet(f"{row['label']} — {row['count']} items, "
                       f"{row['discussion']} questions and comments")

    c.showPage()
    c.save()
    return buf.getvalue()
