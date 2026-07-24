"""
User-level moderation ledger.

Records each escalation step taken against a user — ``warn`` → ``mute`` (24h) →
``suspend`` — plus the reversing actions (``unmute`` / ``unsuspend``). Every
row carries the moderator, a required reason, and (for a mute) when it expires.
This is the immutable history that drives the next escalation step and the
audit trail (MOD-04 / "all actions logged with reason").
"""
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Index

from app.core.database import Base

MOD_ACTIONS = ("warn", "mute", "suspend", "unmute", "unsuspend")


class ModerationAction(Base):
    __tablename__ = "moderation_actions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)     # target
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                      nullable=True)                  # moderator
    action = Column(String(20), nullable=False)       # warn | mute | suspend | …
    reason = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)   # mutes only
    created_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (Index("idx_mod_action_user", "user_id"),)
