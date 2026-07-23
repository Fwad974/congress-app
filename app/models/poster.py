"""
Poster gallery: a browsable poster hall with voting, comments, and a
scavenger-hunt (attendees check in to posters and track progress).

- **Poster** — created by a speaker/organizer; browsable by everyone.
- **PosterVote** — one upvote per (poster, attendee).
- **PosterComment** — attendee discussion on a poster.
- **PosterVisit** — a scavenger-hunt check-in; one per (poster, attendee).
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint, Index,
)
from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class Poster(Base):
    __tablename__ = "posters"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(300), nullable=False)
    authors = Column(Text, nullable=False)          # free-text author list
    category = Column(String(120), nullable=True, index=True)   # track
    abstract = Column(Text, nullable=True)
    board_number = Column(String(30), nullable=True)            # physical location
    external_url = Column(String(500), nullable=True)           # link to full poster
    # Uploaded poster image/PDF. stored_image is a uuid name on disk.
    image_name = Column(String(255), nullable=True)
    stored_image = Column(String(255), nullable=True)
    # Short code printed on the physical poster for scavenger-hunt check-in.
    hunt_code = Column(String(12), nullable=False, unique=True, index=True)

    presenter_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                          nullable=True, index=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                        nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow,
                        onupdate=_utcnow, nullable=False)


class PosterVote(Base):
    __tablename__ = "poster_votes"

    id = Column(Integer, primary_key=True, index=True)
    poster_id = Column(Integer, ForeignKey("posters.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("poster_id", "user_id", name="uq_poster_vote"),
        Index("idx_poster_vote_poster", "poster_id"),
    )


class PosterComment(Base):
    __tablename__ = "poster_comments"

    id = Column(Integer, primary_key=True, index=True)
    poster_id = Column(Integer, ForeignKey("posters.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    __table_args__ = (Index("idx_poster_comment_poster", "poster_id"),)


class PosterVisit(Base):
    __tablename__ = "poster_visits"

    id = Column(Integer, primary_key=True, index=True)
    poster_id = Column(Integer, ForeignKey("posters.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("poster_id", "user_id", name="uq_poster_visit"),
        Index("idx_poster_visit_user", "user_id"),
    )
