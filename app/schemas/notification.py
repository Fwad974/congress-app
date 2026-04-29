"""
Notification preference schemas.
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator


VALID_SOUNDS = {"bell", "chime", "buzz", "off"}
VALID_LEAD_TIMES = {0, 5, 10, 15, 30, 60}  # minutes; 0 = "At start"

DEFAULT_PREFS = {
    "enabled": True,
    "lead_times": [10, 0],   # 10 min before + at start
    "sound": "bell",
    "volume": 0.7,
}


class NotificationPrefs(BaseModel):
    enabled: bool = True
    lead_times: List[int] = [10, 0]
    sound: str = "bell"
    volume: float = 0.7

    @field_validator("lead_times")
    @classmethod
    def clean_lead_times(cls, v):
        cleaned = sorted({int(x) for x in v if int(x) in VALID_LEAD_TIMES}, reverse=True)
        return cleaned

    @field_validator("sound")
    @classmethod
    def valid_sound(cls, v):
        if v not in VALID_SOUNDS:
            raise ValueError(f"Invalid sound: {v}")
        return v

    @field_validator("volume")
    @classmethod
    def valid_volume(cls, v):
        if v < 0:
            return 0.0
        if v > 1:
            return 1.0
        return float(v)


class NotificationSettingsResponse(BaseModel):
    prefs: NotificationPrefs
    updated_at: Optional[datetime] = None


class UpcomingItem(BaseModel):
    """A bookmarked schedule item the client should schedule reminders for."""
    id: int
    title: str
    type: str
    location: Optional[str] = None
    start_time: datetime
    end_time: datetime


class UpcomingResponse(BaseModel):
    """Everything the client-side scheduler needs in one round trip."""
    prefs: NotificationPrefs
    items: List[UpcomingItem]
    server_time: datetime
