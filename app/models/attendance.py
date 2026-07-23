"""
Session attendance — recorded when an attendee scans the session's QR code
(or types its code). Drives the attendance / CME certificates.
"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, DateTime, ForeignKey, UniqueConstraint, Index
from app.core.database import Base


class SessionAttendance(Base):
    __tablename__ = "session_attendance"

    id = Column(Integer, primary_key=True, index=True)
    schedule_item_id = Column(Integer,
                              ForeignKey("schedule_items.id", ondelete="CASCADE"),
                              nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    created_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("schedule_item_id", "user_id", name="uq_session_attendance"),
        Index("idx_attendance_user", "user_id"),
    )
