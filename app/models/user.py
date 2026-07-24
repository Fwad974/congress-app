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
    # Nullable: OAuth-only accounts (Google/ORCID) have no local password.
    hashed_password = Column(String(255), nullable=True)
    full_name = Column(String(255), nullable=False)
    institution = Column(String(255), nullable=True)
    role = Column(SAEnum(UserRole), default=UserRole.attendee, nullable=False)

    # Profile fields
    bio = Column(Text, nullable=True)
    research_interests = Column(ARRAY(String), default=[])
    profile_photo_url = Column(String(500), nullable=True)
    orcid_id = Column(String(50), nullable=True)

    # Opt-in to the attendee networking directory (/connect). Off by default —
    # listing shows name, role, institution, interests, and email.
    networking_visible = Column(Boolean, default=False, nullable=False)

    # Status
    is_active = Column(Boolean, default=True)         # False = suspended
    is_verified = Column(Boolean, default=False)
    two_factor_enabled = Column(Boolean, default=False)
    # Moderation: temporary posting ban (MOD-04 mute step). When set to a
    # future time, the user can browse/read but not create content.
    muted_until = Column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    last_login = Column(DateTime(timezone=True), nullable=True)
