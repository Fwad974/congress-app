"""
Live poll models — chairs run polls during a session (a schedule item);
attendees vote. Three types:

- single    : pick exactly one option
- multiple  : pick any number of options
- wordcloud : submit free-text terms, aggregated into a frequency cloud

Choice polls use PollOption rows; word clouds store the raw terms in
PollResponse.text. Counts are always computed from PollResponse rows so they
can't drift.
"""
import enum
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Enum as SAEnum, ForeignKey,
    UniqueConstraint, Index,
)
from app.core.database import Base


class PollType(str, enum.Enum):
    single = "single"
    multiple = "multiple"
    wordcloud = "wordcloud"


class PollStatus(str, enum.Enum):
    draft = "draft"
    open = "open"
    closed = "closed"


class Poll(Base):
    __tablename__ = "polls"

    id = Column(Integer, primary_key=True, index=True)
    schedule_item_id = Column(
        Integer, ForeignKey("schedule_items.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                        nullable=True)
    question = Column(Text, nullable=False)
    type = Column(SAEnum(PollType), default=PollType.single, nullable=False)
    status = Column(SAEnum(PollStatus), default=PollStatus.draft,
                    nullable=False, index=True)
    created_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("idx_poll_session_status", "schedule_item_id", "status"),
    )


class PollOption(Base):
    __tablename__ = "poll_options"

    id = Column(Integer, primary_key=True, index=True)
    poll_id = Column(Integer, ForeignKey("polls.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    text = Column(String(255), nullable=False)
    position = Column(Integer, default=0, nullable=False)


class PollResponse(Base):
    __tablename__ = "poll_responses"

    id = Column(Integer, primary_key=True, index=True)
    poll_id = Column(Integer, ForeignKey("polls.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    # Choice polls set option_id; word clouds set text.
    option_id = Column(Integer, ForeignKey("poll_options.id", ondelete="CASCADE"),
                       nullable=True, index=True)
    text = Column(String(80), nullable=True)
    created_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        # One row per (poll, user, option) — stops double-counting a choice.
        # NULL option_id rows (word cloud) are exempt (NULLs compare distinct).
        UniqueConstraint("poll_id", "user_id", "option_id", name="uq_poll_response"),
        Index("idx_poll_response_poll", "poll_id"),
    )
