"""
Schedule Model — Congress program / agenda items
Admin-managed; visible to all authenticated users.

Type-specific fields (speaker, panelists, capacity, etc.) live in the
flexible JSON `extra` column — see app.schemas.schedule.EXTRA_SPEC for the
allowed keys per type.
"""
import enum
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, DateTime, Text, Enum as SAEnum, Index, JSON,
    ForeignKey, UniqueConstraint,
)
from app.core.database import Base


class ScheduleType(str, enum.Enum):
    keynote = "keynote"
    talk = "talk"
    panel = "panel"
    workshop = "workshop"
    poster = "poster"
    break_ = "break"
    networking = "networking"
    other = "other"


class ScheduleItem(Base):
    __tablename__ = "schedule_items"

    id = Column(Integer, primary_key=True, index=True)

    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    type = Column(SAEnum(ScheduleType), default=ScheduleType.talk, nullable=False)

    location = Column(String(255), nullable=True)

    # The app account presenting this session (if any). Lets that user
    # self-manage their abstract/slides and moderate the session's Q&A.
    speaker_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                        nullable=True, index=True)

    # Attendance check-in code (printed as a QR at the session door).
    # Generated lazily the first time an admin requests the session's QR.
    attend_code = Column(String(12), nullable=True, unique=True, index=True)

    start_time = Column(DateTime(timezone=True), nullable=False, index=True)
    end_time = Column(DateTime(timezone=True), nullable=False)

    # Type-specific fields (speaker, panelists, capacity, etc.)
    extra = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    created_by = Column(Integer, nullable=True)

    __table_args__ = (
        Index("idx_schedule_start", "start_time"),
    )


class ScheduleBookmark(Base):
    """A single user's bookmark on a schedule item."""
    __tablename__ = "schedule_bookmarks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    schedule_item_id = Column(Integer, ForeignKey("schedule_items.id", ondelete="CASCADE"),
                              nullable=False, index=True)
    created_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "schedule_item_id", name="uq_schedule_bookmark"),
    )
