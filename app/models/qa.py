"""
Live Q&A models — attendees submit questions during a session (a schedule
item) and upvote others'. Moderators/chairs can mark questions answered or
hide them.

`upvotes` is a denormalized count kept in sync with the QuestionVote rows so
listing/sorting stays cheap.
"""
import enum
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, Text, DateTime, Enum as SAEnum, ForeignKey,
    UniqueConstraint, Index,
)
from app.core.database import Base


class QuestionStatus(str, enum.Enum):
    open = "open"          # visible, awaiting an answer
    answered = "answered"  # addressed by the speaker
    hidden = "hidden"      # removed by a moderator


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    schedule_item_id = Column(
        Integer, ForeignKey("schedule_items.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    text = Column(Text, nullable=False)
    status = Column(SAEnum(QuestionStatus), default=QuestionStatus.open,
                    nullable=False, index=True)
    upvotes = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("idx_question_session_status", "schedule_item_id", "status"),
    )


class QuestionVote(Base):
    __tablename__ = "question_votes"

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    created_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("question_id", "user_id", name="uq_question_vote"),
    )
