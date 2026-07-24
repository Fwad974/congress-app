"""
Venue & Dubai Info schemas.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, field_validator

from app.models.venue import ROOM_KINDS

# The editable settings sections. PUT /api/venue/settings accepts any subset.
SETTING_KEYS = ("general", "wifi", "transport", "emergency", "places")


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


class RoomCreate(BaseModel):
    name: str
    name_ar: Optional[str] = None
    kind: str = "room"
    floor: int = 1
    x: float = 10
    y: float = 10
    w: float = 20
    h: float = 15
    directions_hint: Optional[str] = None
    directions_hint_ar: Optional[str] = None

    @field_validator("name")
    @classmethod
    def v_name(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Room name is required")
        return v[:120]

    @field_validator("name_ar")
    @classmethod
    def v_name_ar(cls, v):
        return (v or "").strip()[:120] or None

    @field_validator("kind")
    @classmethod
    def v_kind(cls, v):
        if v not in ROOM_KINDS:
            raise ValueError(f"Kind must be one of: {', '.join(ROOM_KINDS)}")
        return v

    @field_validator("floor")
    @classmethod
    def v_floor(cls, v):
        if not (-3 <= int(v) <= 50):
            raise ValueError("Floor must be between -3 and 50")
        return int(v)

    @field_validator("x", "y")
    @classmethod
    def v_pos(cls, v):
        return _clip(v, 0, 100)

    @field_validator("w", "h")
    @classmethod
    def v_size(cls, v):
        return _clip(v, 2, 100)


class RoomUpdate(BaseModel):
    name: Optional[str] = None
    name_ar: Optional[str] = None
    kind: Optional[str] = None
    floor: Optional[int] = None
    x: Optional[float] = None
    y: Optional[float] = None
    w: Optional[float] = None
    h: Optional[float] = None
    directions_hint: Optional[str] = None
    directions_hint_ar: Optional[str] = None

    @field_validator("kind")
    @classmethod
    def v_kind(cls, v):
        if v is not None and v not in ROOM_KINDS:
            raise ValueError(f"Kind must be one of: {', '.join(ROOM_KINDS)}")
        return v

    @field_validator("x", "y")
    @classmethod
    def v_pos(cls, v):
        return None if v is None else _clip(v, 0, 100)

    @field_validator("w", "h")
    @classmethod
    def v_size(cls, v):
        return None if v is None else _clip(v, 2, 100)


class SessionBrief(BaseModel):
    id: int
    title: str
    start_time: datetime
    end_time: datetime


class RoomOut(BaseModel):
    id: int
    name: str
    name_ar: Optional[str] = None
    kind: str
    floor: int
    x: float
    y: float
    w: float
    h: float
    directions_hint: Optional[str] = None
    directions_hint_ar: Optional[str] = None
    now: Optional[SessionBrief] = None     # session happening in this room now
    next: Optional[SessionBrief] = None    # next upcoming session here


class VenueResponse(BaseModel):
    general: Dict[str, Any]
    wifi: Dict[str, Any]
    transport: List[Dict[str, Any]]
    emergency: List[Dict[str, Any]]
    places: List[Dict[str, Any]]
    rooms: List[RoomOut]
    floors: List[int]
    can_manage: bool = False


class SettingsUpdate(BaseModel):
    """Partial update — only the provided sections are replaced."""
    general: Optional[Dict[str, Any]] = None
    wifi: Optional[Dict[str, Any]] = None
    transport: Optional[List[Dict[str, Any]]] = None
    emergency: Optional[List[Dict[str, Any]]] = None
    places: Optional[List[Dict[str, Any]]] = None


class RouteStep(BaseModel):
    en: str
    ar: str


class RouteResponse(BaseModel):
    from_room: str
    to_room: str
    distance_m: int
    steps: List[RouteStep]
