"""
Session recording schemas.
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator

from app.models.recording import CONSENT_STATES


def _url(v: Optional[str]) -> Optional[str]:
    v = (v or "").strip()
    if not v:
        return None
    if not v.startswith(("http://", "https://", "/")):
        raise ValueError("Link must start with http://, https:// or /")
    return v[:1000]


class RecordingCreate(BaseModel):
    schedule_item_id: int
    video_url: Optional[str] = None
    slides_url: Optional[str] = None
    duration_seconds: Optional[int] = None

    @field_validator("video_url", "slides_url")
    @classmethod
    def v_url(cls, v):
        return _url(v)

    @field_validator("duration_seconds")
    @classmethod
    def v_duration(cls, v):
        if v is None:
            return None
        if v < 0:
            raise ValueError("Duration cannot be negative")
        return min(int(v), 24 * 3600)


class RecordingUpdate(BaseModel):
    video_url: Optional[str] = None
    slides_url: Optional[str] = None
    duration_seconds: Optional[int] = None
    # Organizers may extend or shorten the availability window.
    available_until: Optional[datetime] = None

    @field_validator("video_url", "slides_url")
    @classmethod
    def v_url(cls, v):
        return _url(v)

    @field_validator("duration_seconds")
    @classmethod
    def v_duration(cls, v):
        if v is None:
            return None
        if v < 0:
            raise ValueError("Duration cannot be negative")
        return min(int(v), 24 * 3600)


class ConsentRequest(BaseModel):
    """The speaker's recording-consent decision (REC-01)."""
    decision: str  # granted | denied
    note: Optional[str] = None

    @field_validator("decision")
    @classmethod
    def v_decision(cls, v):
        v = (v or "").strip().lower()
        if v not in ("granted", "denied"):
            raise ValueError("Decision must be 'granted' or 'denied'")
        return v

    @field_validator("note")
    @classmethod
    def v_note(cls, v):
        return (v or "").strip()[:500] or None


class SegmentIn(BaseModel):
    start_seconds: int = 0
    end_seconds: Optional[int] = None
    speaker_label: Optional[str] = None
    text: str

    @field_validator("start_seconds")
    @classmethod
    def v_start(cls, v):
        return max(0, int(v or 0))

    @field_validator("end_seconds")
    @classmethod
    def v_end(cls, v):
        return None if v is None else max(0, int(v))

    @field_validator("speaker_label")
    @classmethod
    def v_label(cls, v):
        return (v or "").strip()[:120] or None

    @field_validator("text")
    @classmethod
    def v_text(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Segment text is required")
        return v[:4000]


class TranscriptUpload(BaseModel):
    """Replace a recording's transcript.

    Either post structured ``segments`` or paste a WebVTT/SRT ``vtt`` blob —
    the server parses it into timestamped segments.
    """
    segments: Optional[List[SegmentIn]] = None
    vtt: Optional[str] = None


class SlideIn(BaseModel):
    at_seconds: int = 0
    slide_number: int = 1
    title: Optional[str] = None
    image_url: Optional[str] = None

    @field_validator("at_seconds")
    @classmethod
    def v_at(cls, v):
        return max(0, int(v or 0))

    @field_validator("slide_number")
    @classmethod
    def v_num(cls, v):
        return max(1, int(v or 1))

    @field_validator("title")
    @classmethod
    def v_title(cls, v):
        return (v or "").strip()[:300] or None

    @field_validator("image_url")
    @classmethod
    def v_img(cls, v):
        return _url(v)


class SlidesUpload(BaseModel):
    slides: List[SlideIn]


class SegmentOut(BaseModel):
    id: int
    start_seconds: int
    end_seconds: Optional[int] = None
    speaker_label: Optional[str] = None
    text: str


class SlideOut(BaseModel):
    id: int
    at_seconds: int
    slide_number: int
    title: Optional[str] = None
    image_url: Optional[str] = None


class RecordingOut(BaseModel):
    id: int
    schedule_item_id: int
    session_title: str
    session_start: Optional[datetime] = None
    speaker_name: Optional[str] = None
    video_url: Optional[str] = None
    slides_url: Optional[str] = None
    duration_seconds: Optional[int] = None
    status: str
    consent_state: str
    consent_note: Optional[str] = None
    published_at: Optional[datetime] = None
    available_until: Optional[datetime] = None
    days_left: Optional[int] = None
    expired: bool = False
    available: bool = False       # visible to attendees right now
    views: int = 0
    segment_count: int = 0
    slide_count: int = 0
    can_manage: bool = False      # organizer
    can_consent: bool = False     # this user is the session's speaker
    # Populated on the detail endpoint only.
    transcript: List[SegmentOut] = []
    slides: List[SlideOut] = []


class SearchHit(BaseModel):
    recording_id: int
    schedule_item_id: int
    session_title: str
    start_seconds: int
    speaker_label: Optional[str] = None
    text: str


class SearchResponse(BaseModel):
    query: str
    total: int
    hits: List[SearchHit]


CONSENT_CHOICES = tuple(c for c in CONSENT_STATES if c != "pending")
