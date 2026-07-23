"""
Feature flags — admin-controlled release switches for optional modules.

Each row toggles a whole feature (Papers, Poster Gallery, …) on or off for
attendees. Admins flip these from the Releases tab when a feature is ready to
go live. Unknown / unseeded keys are treated as enabled (fail-open).
"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from app.core.database import Base


class FeatureFlag(Base):
    __tablename__ = "feature_flags"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(50), nullable=False, unique=True, index=True)
    enabled = Column(Boolean, default=True, nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                        nullable=True)
    updated_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)
