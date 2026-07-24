"""
Notification preference schemas.
"""
import re
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


VALID_SOUNDS = {"bell", "chime", "buzz", "off"}
VALID_LEAD_TIMES = {0, 5, 10, 15, 30, 60}  # minutes; 0 = "At start"
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")  # HH:MM 24h

DEFAULT_PREFS = {
    "enabled": True,
    "lead_times": [10, 0],   # 10 min before + at start
    "sound": "bell",
    "volume": 0.7,
    "quiet_hours": {"enabled": False, "start": "22:00", "end": "07:00"},
    "tz": "",                # IANA timezone, set by the client for server-side quiet hours
}


class QuietHours(BaseModel):
    """A do-not-disturb window; non-emergency alerts are held inside it."""
    enabled: bool = False
    start: str = "22:00"
    end: str = "07:00"

    @field_validator("start", "end")
    @classmethod
    def valid_time(cls, v):
        if not _TIME_RE.match(v or ""):
            raise ValueError("Time must be HH:MM (24-hour)")
        return v


class NotificationPrefs(BaseModel):
    enabled: bool = True
    lead_times: List[int] = [10, 0]
    sound: str = "bell"
    volume: float = 0.7
    quiet_hours: QuietHours = Field(default_factory=QuietHours)
    tz: str = ""

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

    @field_validator("tz")
    @classmethod
    def clean_tz(cls, v):
        return (v or "")[:64]


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


class FeedItem(BaseModel):
    """A delivered notification in the user's feed."""
    id: int
    kind: str
    title: str
    body: str
    schedule_item_id: Optional[int] = None
    read: bool
    created_at: datetime


class FeedResponse(BaseModel):
    items: List[FeedItem]
    unread_count: int
    server_time: datetime


# ─── Web Push ────────────────────────────────────────────────────
class PushKeyResponse(BaseModel):
    """Tells the client whether push is configured + the VAPID public key."""
    enabled: bool
    public_key: str = ""


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionIn(BaseModel):
    """Mirrors the browser PushSubscription.toJSON() shape."""
    endpoint: str
    keys: PushKeys


class PushUnsubscribeIn(BaseModel):
    endpoint: str


# ─── Broadcasts / announcements ──────────────────────────────────
VALID_ROLES = {
    "attendee", "speaker", "reviewer", "session_chair", "review_chair",
    "moderator", "admin", "super_admin",
}
MAX_BROADCAST_TITLE = 140
MAX_BROADCAST_BODY = 1000


class AudienceSpec(BaseModel):
    """Targeting filters for an announcement. Filters are ANDed together; any
    explicitly-picked `user_ids` are added on top (union). All empty = everyone."""
    roles: List[str] = []                       # role values
    paper_status: List[str] = []                # authored a paper in these statuses
    is_reviewer: bool = False                   # assigned to review any paper
    is_speaker: bool = False                    # presenter of any session
    attended_min: Optional[int] = None          # checked in to ≥ N sessions
    registered_after: Optional[str] = None      # ISO date/datetime
    registered_before: Optional[str] = None     # ISO date/datetime
    user_ids: List[int] = []                    # specific people (by id)

    @field_validator("roles")
    @classmethod
    def clean_roles(cls, v):
        return [r for r in dict.fromkeys(v or []) if r in VALID_ROLES]

    @field_validator("attended_min")
    @classmethod
    def clean_attended(cls, v):
        if v is None:
            return v
        return max(1, int(v))


class BroadcastRequest(BaseModel):
    title: str
    body: str
    target_roles: List[str] = []   # legacy: empty = everyone (folded into audience.roles)
    audience: Optional[AudienceSpec] = None
    emergency: bool = False

    @field_validator("title")
    @classmethod
    def clean_title(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Title is required")
        return v[:MAX_BROADCAST_TITLE]

    @field_validator("body")
    @classmethod
    def clean_body(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Message is required")
        return v[:MAX_BROADCAST_BODY]

    @field_validator("target_roles")
    @classmethod
    def clean_roles(cls, v):
        return [r for r in dict.fromkeys(v or []) if r in VALID_ROLES]


class BroadcastItem(BaseModel):
    id: int
    title: str
    body: str
    target_roles: List[str]
    audience_summary: Optional[str] = None
    emergency: bool
    recipient_count: int
    actor_email: Optional[str] = None
    created_at: datetime


class BroadcastListResponse(BaseModel):
    broadcasts: List[BroadcastItem]
    sent_today: int
    daily_limit: int


class AudiencePreviewRequest(BaseModel):
    audience: Optional[AudienceSpec] = None
    target_roles: List[str] = []


class RecipientPreview(BaseModel):
    id: int
    full_name: str
    email: str


class AudiencePreviewResponse(BaseModel):
    count: int
    summary: str
    sample: List[RecipientPreview] = []   # first few recipients
