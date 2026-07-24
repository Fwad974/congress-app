"""
Feedback & Ratings.

- **SessionRating** — one per (session, attendee): a 1–5 star rating with an
  optional public comment and an optional **speaker_feedback** note that only
  organizers ever see (never the speaker). Drives the organizer sentiment
  dashboard and the anonymized speaker digest.
- **SurveyResponse** — one per attendee: the post-event satisfaction survey.
  Completing it unlocks the participation certificate.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, Text, Boolean, DateTime, ForeignKey, UniqueConstraint, Index,
)
from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class SessionRating(Base):
    __tablename__ = "session_ratings"

    id = Column(Integer, primary_key=True, index=True)
    schedule_item_id = Column(Integer,
                              ForeignKey("schedule_items.id", ondelete="CASCADE"),
                              nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    stars = Column(Integer, nullable=False)          # 1–5
    comment = Column(Text, nullable=True)            # about the session (anonymized to speaker)
    speaker_feedback = Column(Text, nullable=True)   # about the speaker — organizers only
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow,
                        onupdate=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("schedule_item_id", "user_id", name="uq_session_rating"),
        Index("idx_rating_session", "schedule_item_id"),
    )


class SurveyResponse(Base):
    __tablename__ = "survey_responses"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, unique=True, index=True)
    overall = Column(Integer, nullable=False)        # 1–5 overall satisfaction
    organization = Column(Integer, nullable=True)    # 1–5
    venue = Column(Integer, nullable=True)           # 1–5
    would_recommend = Column(Boolean, nullable=True)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
