"""
Note model — free-form notes a user writes for themselves.

A note can optionally be linked to a schedule item (e.g. takeaways from a talk)
or stand alone as a personal note. Super admins can view all notes and delete
them for moderation.
"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, Text, DateTime, ForeignKey, Index
from app.core.database import Base


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    # Optional link to a session. NULL = standalone note; SET NULL if the
    # session is later deleted so the note (and its text) survives.
    schedule_item_id = Column(
        Integer, ForeignKey("schedule_items.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("idx_note_user_created", "user_id", "created_at"),
    )
