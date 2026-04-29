"""
Schedule Model — Congress program / agenda items
Admin-managed; visible to all authenticated users.

Type-specific fields (speaker, panelists, capacity, etc.) live in the
flexible JSON `extra` column — see app.schemas.schedule.EXTRA_SPEC for the
allowed keys per type.
"""
import enum
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Text, Enum as SAEnum, Index, JSON
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
