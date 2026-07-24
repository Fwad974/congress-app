"""
Sponsor & Exhibitor Portal.

- **Sponsor** — a partner company with a tier (platinum/gold/silver/bronze), a
  tier-styled directory card, and a virtual booth (about text, promo video,
  brochure link, team bios). Created and managed by organizers (admins).
- **SponsorLead** — an attendee's *opt-in* expression of interest, shared with
  the sponsor as a lead (DATA-06: explicit consent required, one per
  attendee/sponsor).
- **SponsorVisit** — booth-visit analytics: one row per (sponsor, attendee)
  with a repeat-visit counter, so organizers get unique visitors + total
  visits + engagement without unbounded row growth.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, ForeignKey,
    UniqueConstraint, Index, JSON,
)

from app.core.database import Base

# Tier -> display rank (lower = higher up the page / larger card).
SPONSOR_TIERS = ["platinum", "gold", "silver", "bronze"]
TIER_RANK = {t: i for i, t in enumerate(SPONSOR_TIERS)}


def _utcnow():
    return datetime.now(timezone.utc)


class Sponsor(Base):
    __tablename__ = "sponsors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    tier = Column(String(20), nullable=False, default="silver", index=True)
    # Directory card
    tagline = Column(String(300), nullable=True)     # one-liner under the name
    website_url = Column(String(500), nullable=True)
    logo_name = Column(String(255), nullable=True)   # original upload name
    stored_logo = Column(String(255), nullable=True)  # opaque uuid name on disk
    # Virtual booth (rich media)
    about = Column(Text, nullable=True)              # booth description
    video_url = Column(String(500), nullable=True)   # promo video (embed/link)
    brochure_url = Column(String(500), nullable=True)  # PDF / external brochure
    booth_number = Column(String(30), nullable=True)  # physical booth on the floor
    team = Column(JSON, nullable=True)               # [{name, title, bio}]
    # Visibility / ordering
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    display_order = Column(Integer, nullable=False, default=0)

    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                        nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow,
                        onupdate=_utcnow, nullable=False)


class SponsorLead(Base):
    __tablename__ = "sponsor_leads"

    id = Column(Integer, primary_key=True, index=True)
    sponsor_id = Column(Integer, ForeignKey("sponsors.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    # DATA-06: an affirmative, recorded consent to share contact details.
    consent = Column(Boolean, nullable=False, default=False)
    message = Column(Text, nullable=True)            # optional note to the sponsor
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("sponsor_id", "user_id", name="uq_sponsor_lead"),
        Index("idx_sponsor_lead_sponsor", "sponsor_id"),
    )


class SponsorVisit(Base):
    __tablename__ = "sponsor_visits"

    id = Column(Integer, primary_key=True, index=True)
    sponsor_id = Column(Integer, ForeignKey("sponsors.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    visits = Column(Integer, nullable=False, default=1)   # repeat opens
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_visit_at = Column(DateTime(timezone=True), default=_utcnow,
                           onupdate=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("sponsor_id", "user_id", name="uq_sponsor_visit"),
        Index("idx_sponsor_visit_sponsor", "sponsor_id"),
    )
