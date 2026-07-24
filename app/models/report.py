"""
Content reports — attendees flag user-generated content (Q&A questions, poster
comments) for a moderator to triage in the moderation queue.
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint, Index,
)
from app.core.database import Base

# Supported reportable content types.
CONTENT_TYPES = ("question", "poster_comment")
REPORT_STATUSES = ("open", "resolved", "dismissed")


class ContentReport(Base):
    __tablename__ = "content_reports"

    id = Column(Integer, primary_key=True, index=True)
    content_type = Column(String(32), nullable=False, index=True)  # question / poster_comment
    content_id = Column(Integer, nullable=False, index=True)
    reporter_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    reason = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="open", index=True)
    action_taken = Column(String(32), nullable=True)   # removed / dismissed
    resolved_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                         nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        # One open report per user per item (re-reporting the same thing is a no-op).
        UniqueConstraint("content_type", "content_id", "reporter_id",
                         name="uq_report_per_user"),
        Index("idx_report_status", "status"),
    )
