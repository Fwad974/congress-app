"""
OAuth account links — one row per (provider, provider account) bound to a
local user. A user may link several providers (Google + ORCID) and still keep
password login, so this is a separate table rather than columns on User.
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, Index,
)
from app.core.database import Base


class OAuthAccount(Base):
    __tablename__ = "oauth_accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    provider = Column(String(20), nullable=False)          # "google" | "orcid"
    provider_user_id = Column(String(255), nullable=False)  # the provider's stable id (sub)
    created_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_oauth_provider_account"),
        Index("idx_oauth_user", "user_id"),
    )
