"""
Notification preferences — one row per user, JSON `prefs` column so we can
evolve the shape without migrations.

Schema of `prefs` (see app.schemas.notification.DEFAULT_PREFS):
    {
        "enabled":   bool,
        "lead_times":[int, ...],   # minutes; 0 means "at start"
        "sound":     "bell" | "chime" | "buzz" | "off",
        "volume":    float (0.0 – 1.0),
    }
"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, DateTime, JSON, ForeignKey, UniqueConstraint
from app.core.database import Base


class NotificationSettings(Base):
    __tablename__ = "notification_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, unique=True, index=True)
    prefs = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_notification_settings_user"),
    )
