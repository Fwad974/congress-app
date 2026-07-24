"""
Transcript helpers — parse WebVTT/SRT captions into timestamped segments and
search them.

Recording platforms all export captions, so accepting a pasted VTT/SRT blob is
the lowest-friction way to get a searchable transcript into the app. The parser
is deliberately tolerant: it accepts ``hh:mm:ss.mmm`` and ``mm:ss,mmm`` cue
times, optional cue numbers/identifiers, WebVTT ``<v Speaker>`` voice tags and
plain ``Speaker:`` prefixes.
"""
import re
from typing import Dict, List

_CUE_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}|\d{1,2}:\d{2}(?::\d{2})?)"
    r"\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}|\d{1,2}:\d{2}(?::\d{2})?)"
)
_VOICE_RE = re.compile(r"<v\.?[^ >]*\s+([^>]+)>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_SPEAKER_RE = re.compile(r"^([A-Z][\w .'\-]{1,40}):\s+(.*)$")


def parse_timestamp(value: str) -> int:
    """``00:12:30.500`` / ``12:30`` → whole seconds."""
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 2:
        parts = ["0"] + parts
    if len(parts) != 3:
        raise ValueError(f"Bad timestamp: {value}")
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + int(float(seconds))


def format_timestamp(seconds: int) -> str:
    """Seconds → ``h:mm:ss`` (or ``m:ss`` under an hour) for display."""
    seconds = max(0, int(seconds or 0))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def parse_vtt(blob: str) -> List[Dict]:
    """Parse a WebVTT/SRT blob into ``[{start_seconds, end_seconds,
    speaker_label, text}, …]``. Unparseable cues are skipped rather than
    failing the whole upload."""
    segments: List[Dict] = []
    if not blob:
        return segments

    # Split into cue blocks on blank lines; a block is "[id] timing + text".
    for block in re.split(r"\r?\n\s*\r?\n", blob.replace("﻿", "").strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        timing_idx = next((i for i, ln in enumerate(lines) if _CUE_RE.search(ln)), None)
        if timing_idx is None:
            continue  # header ("WEBVTT"), NOTE, or a stray block
        match = _CUE_RE.search(lines[timing_idx])
        try:
            start = parse_timestamp(match.group("start"))
            end = parse_timestamp(match.group("end"))
        except ValueError:
            continue

        speaker = None
        body_lines = []
        for raw in lines[timing_idx + 1:]:
            voice = _VOICE_RE.search(raw)
            if voice:
                speaker = speaker or voice.group(1).strip()
            clean = _TAG_RE.sub("", raw).strip()
            if not clean:
                continue
            if speaker is None:
                named = _SPEAKER_RE.match(clean)
                if named:
                    speaker, clean = named.group(1).strip(), named.group(2).strip()
            body_lines.append(clean)

        text = " ".join(body_lines).strip()
        if not text:
            continue
        segments.append({
            "start_seconds": start,
            "end_seconds": end if end >= start else None,
            "speaker_label": (speaker or None) and speaker[:120],
            "text": text[:4000],
        })
    return segments


def snippet(text: str, query: str, width: int = 160) -> str:
    """A short excerpt of ``text`` centred on the first match of ``query``."""
    if not text:
        return ""
    idx = text.lower().find((query or "").lower()) if query else -1
    if idx < 0 or len(text) <= width:
        return text[:width] + ("…" if len(text) > width else "")
    start = max(0, idx - width // 3)
    end = min(len(text), start + width)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")
