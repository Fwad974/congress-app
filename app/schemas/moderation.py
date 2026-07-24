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
    reasons: List[str] = []
    report_count: int = 1
    first_reported_at: datetime


class ReportListResponse(BaseModel):
    reports: List[ReportItem]
    open_count: int


class ResolveRequest(BaseModel):
    action: str   # "remove" | "dismiss"

    @field_validator("action")
    @classmethod
    def v_action(cls, v):
        if v not in ("remove", "dismiss"):
            raise ValueError("Action must be remove or dismiss")
        return v


class ModStats(BaseModel):
    open: int
    resolved: int
    dismissed: int
    by_type: dict
