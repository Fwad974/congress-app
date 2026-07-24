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
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, JSON, Boolean, ForeignKey,
    UniqueConstraint, Index,
)
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


class UserNotification(Base):
    """A single delivered notification in a user's feed.

    Created server-side when an admin changes a schedule item the user has
    bookmarked (kind="updated") or cancels it (kind="cancelled"). The
    client-side scheduler polls /api/notifications/feed and surfaces unread
    rows as a toast + (optional) Web Notification + sound, then marks them read.

    `title` and `body` are denormalized snapshots so the feed stays meaningful
    even after the underlying schedule item is deleted (schedule_item_id is then
    set NULL).
    """
    __tablename__ = "user_notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    schedule_item_id = Column(
        Integer, ForeignKey("schedule_items.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    kind = Column(String(32), nullable=False, default="updated")
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc),
                        nullable=False, index=True)
    read_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_user_notification_unread", "user_id", "read_at"),
    )


class PushSubscription(Base):
    """A browser Web Push subscription (one per device/browser per user).

    Populated by the client after the user grants notification permission; used
    by app.core.push_service to deliver pushes even when the app tab is closed.
    The `endpoint` is unique — a browser hands out a stable endpoint per
    subscription, so re-subscribing upserts rather than duplicating.
    """
    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    endpoint = Column(Text, nullable=False, unique=True)
    p256dh = Column(String(255), nullable=False)
    auth = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc), nullable=False)


class Broadcast(Base):
    """An organizer announcement / emergency alert sent to a set of users.

    One row per broadcast (the per-user copies live in UserNotification with
    kind 'announcement' or 'emergency'). Kept for history + the NOTIF-02
    daily-limit check and admin display.
    """
    __tablename__ = "broadcasts"

    id = Column(Integer, primary_key=True, index=True)
    actor_id = Column(Integer, nullable=True)
    actor_email = Column(String(255), nullable=True)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    target_roles = Column(JSON, nullable=False, default=list)  # [] = everyone
    # Human-readable description of the targeted audience (roles + filters).
    audience_summary = Column(String(500), nullable=True)
    emergency = Column(Boolean, nullable=False, default=False)
    recipient_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc),
                        nullable=False, index=True)
