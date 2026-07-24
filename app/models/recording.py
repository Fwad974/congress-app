"""
Session recordings: consent-gated video with a searchable transcript and
slide-sync markers, available for a limited window after the congress.

- **SessionRecording** — one per schedule item. It only becomes visible to
  attendees when the presenting speaker has *granted consent* AND an organizer
  has published it AND the availability window (default 30 days) is still open.
- **TranscriptSegment** — a timestamped chunk of speech. Searching returns the
  segment's ``start_seconds`` so the player can seek straight to the moment.
- **SlideMarker** — "slide N starts at t", which drives slide-sync playback.
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint, Index,
)
from app.core.database import Base

# Publication lifecycle, controlled by organizers.
RECORDING_STATUSES = ("draft", "published", "withheld")
# Speaker consent (REC-01). Nothing is published without an explicit grant.
CONSENT_STATES = ("pending", "granted", "denied")


def _utcnow():
    return datetime.now(timezone.utc)


class SessionRecording(Base):
    __tablename__ = "session_recordings"

    id = Column(Integer, primary_key=True, index=True)
    schedule_item_id = Column(Integer,
                              ForeignKey("schedule_items.id", ondelete="CASCADE"),
                              nullable=False, unique=True, index=True)

    # Where the media lives. A direct media URL (…/talk.mp4) plays inline;
    # anything else is offered as a link (e.g. an embedded platform page).
    video_url = Column(String(1000), nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    # Optional slide deck (PDF/slides link) shown next to the player.
    slides_url = Column(String(1000), nullable=True)

    status = Column(String(12), nullable=False, default="draft", index=True)

    # Consent (granted/denied by the session's speaker, or recorded on their
    # behalf by an organizer holding a signed release — always audit-logged).
    consent_state = Column(String(10), nullable=False, default="pending", index=True)
    consent_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                        nullable=True)
    consent_at = Column(DateTime(timezone=True), nullable=True)
    consent_note = Column(Text, nullable=True)

    published_at = Column(DateTime(timezone=True), nullable=True)
    # End of the post-event availability window (REC-04). Set on publish from
    # RECORDING_RETENTION_DAYS; organizers may extend or clear it.
    available_until = Column(DateTime(timezone=True), nullable=True, index=True)

    # Denormalized view counter (one bump per playback request).
    views = Column(Integer, nullable=False, default=0)

    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                        nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow,
                        onupdate=_utcnow, nullable=False)


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id = Column(Integer, primary_key=True, index=True)
    recording_id = Column(Integer,
                          ForeignKey("session_recordings.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    start_seconds = Column(Integer, nullable=False, default=0)
    end_seconds = Column(Integer, nullable=True)
    speaker_label = Column(String(120), nullable=True)
    text = Column(Text, nullable=False)

    __table_args__ = (
        Index("idx_transcript_recording_start", "recording_id", "start_seconds"),
    )


class SlideMarker(Base):
    __tablename__ = "slide_markers"

    id = Column(Integer, primary_key=True, index=True)
    recording_id = Column(Integer,
                          ForeignKey("session_recordings.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    at_seconds = Column(Integer, nullable=False, default=0)
    slide_number = Column(Integer, nullable=False, default=1)
    title = Column(String(300), nullable=True)
    image_url = Column(String(1000), nullable=True)

    __table_args__ = (
        UniqueConstraint("recording_id", "slide_number", name="uq_slide_marker"),
        Index("idx_slide_recording_at", "recording_id", "at_seconds"),
    )
