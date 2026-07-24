"""
Venue & Dubai Info — all content is organizer-managed (nothing hardcoded in
templates), per-key with sensible Dubai defaults served until an admin edits.

- **VenueRoom** — a room/hall/facility placed on the schematic floor plan
  (grid coordinates 0–100 per floor). Room names are matched (case-insensitive)
  against ``ScheduleItem.location`` to overlay the current/next session, and
  the coordinates drive the turn-by-turn route generator.
- **VenueSetting** — key → JSON blob for the editable sections: ``general``
  (venue name/address), ``wifi``, ``transport``, ``emergency``, ``places``.
  Every free-text field can carry an ``…_ar`` Arabic counterpart for the
  English/Arabic toggle.
"""
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Float, Text, DateTime, JSON

from app.core.database import Base

ROOM_KINDS = ("room", "hall", "facility")


def _utcnow():
    return datetime.now(timezone.utc)


class VenueRoom(Base):
    __tablename__ = "venue_rooms"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    name_ar = Column(String(120), nullable=True)
    kind = Column(String(12), nullable=False, default="room")  # room|hall|facility
    floor = Column(Integer, nullable=False, default=1, index=True)
    # Schematic grid coordinates (0–100 per floor), 1 unit ≈ 1 metre.
    x = Column(Float, nullable=False, default=10)
    y = Column(Float, nullable=False, default=10)
    w = Column(Float, nullable=False, default=20)
    h = Column(Float, nullable=False, default=15)
    # Optional human hint appended to generated directions ("next to the café").
    directions_hint = Column(Text, nullable=True)
    directions_hint_ar = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow,
                        onupdate=_utcnow, nullable=False)


class VenueSetting(Base):
    __tablename__ = "venue_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(40), nullable=False, unique=True, index=True)
    value = Column(JSON, nullable=False)
    updated_by = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow,
                        onupdate=_utcnow, nullable=False)
