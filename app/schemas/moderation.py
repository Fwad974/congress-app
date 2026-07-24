"""
Content moderation schemas.
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator

from app.models.report import CONTENT_TYPES


class ReportCreate(BaseModel):
    content_type: str
    content_id: int
    reason: Optional[str] = None

    @field_validator("content_type")
    @classmethod
    def v_type(cls, v):
        if v not in CONTENT_TYPES:
            raise ValueError("Unknown content type")
        return v

    @field_validator("reason")
    @classmethod
    def v_reason(cls, v):
        return (v or "").strip()[:500] or None


class ReportItem(BaseModel):
    id: int                      # representative report id (use to resolve)
    content_type: str
    content_id: int
    content_preview: Optional[str] = None
    content_context: Optional[str] = None   # e.g. session / poster title
    content_author: Optional[str] = None
    content_author_id: Optional[int] = None  # for taking action on the author
    reasons: List[str] = []
    report_count: int = 1
    auto_flagged: bool = False               # system filter flagged this
    first_reported_at: datetime


class ReportListResponse(BaseModel):
    reports: List[ReportItem]
    open_count: int


class ResolveRequest(BaseModel):
    action: str   # "remove" | "dismiss"
    reason: Optional[str] = None

    @field_validator("action")
    @classmethod
    def v_action(cls, v):
        if v not in ("remove", "dismiss"):
            raise ValueError("Action must be remove or dismiss")
        return v

    @field_validator("reason")
    @classmethod
    def v_reason(cls, v):
        return (v or "").strip()[:500] or None


class ModStats(BaseModel):
    open: int
    resolved: int
    dismissed: int
    pending_posters: int = 0
    by_type: dict


# ─── Poster approval ─────────────────────────────────────────────
class PendingPoster(BaseModel):
    id: int
    title: str
    authors: str
    category: Optional[str] = None
    abstract: Optional[str] = None
    presenter_name: Optional[str] = None
    has_image: bool = False
    created_at: datetime


class ApprovalRequest(BaseModel):
    reason: Optional[str] = None

    @field_validator("reason")
    @classmethod
    def v_reason(cls, v):
        return (v or "").strip()[:500] or None


# ─── User escalation (warn → mute → suspend) ─────────────────────
class UserActionRequest(BaseModel):
    action: str   # warn | mute | suspend | unmute | unsuspend
    reason: str

    @field_validator("action")
    @classmethod
    def v_action(cls, v):
        from app.models.moderation import MOD_ACTIONS
        if v not in MOD_ACTIONS:
            raise ValueError(f"Action must be one of: {', '.join(MOD_ACTIONS)}")
        return v

    @field_validator("reason")
    @classmethod
    def v_reason(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("A reason is required")
        return v[:500]


class EscalateRequest(BaseModel):
    reason: str

    @field_validator("reason")
    @classmethod
    def v_reason(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("A reason is required")
        return v[:500]


class ModActionItem(BaseModel):
    id: int
    action: str
    reason: str
    actor_id: Optional[int] = None
    expires_at: Optional[datetime] = None
    created_at: datetime


class UserModerationState(BaseModel):
    user_id: int
    name: str
    email: str
    is_active: bool
    muted: bool
    muted_until: Optional[datetime] = None
    next_step: Optional[str] = None      # warn | mute | suspend | None
    applied: Optional[str] = None        # the action just applied (escalate)
    history: List[ModActionItem] = []
