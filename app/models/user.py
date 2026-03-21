import enum
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Enum as SAEnum, Text, ARRAY
from app.core.database import Base


class UserRole(str, enum.Enum):
    attendee = "attendee"
    speaker = "speaker"
    reviewer = "reviewer"
    session_chair = "session_chair"
    review_chair = "review_chair"
    moderator = "moderator"
    admin = "admin"
    super_admin = "super_admin"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    institution = Column(String(255), nullable=True)
    role = Column(SAEnum(UserRole), default=UserRole.attendee, nullable=False)

    # Profile fields
    bio = Column(Text, nullable=True)
    research_interests = Column(ARRAY(String), default=[])
    profile_photo_url = Column(String(500), nullable=True)
    orcid_id = Column(String(50), nullable=True)

    # Status
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    two_factor_enabled = Column(Boolean, default=False)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    last_login = Column(DateTime(timezone=True), nullable=True)
