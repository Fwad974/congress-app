"""
Admin Schemas — Request/Response models for User Management Dashboard
"""
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, List
from datetime import datetime


# ─── User Management ─────────────────────────────────────────────
class AdminUserListParams(BaseModel):
    page: int = 1
    per_page: int = 20
    search: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None  # active, suspended, all
    sort_by: Optional[str] = "created_at"
    sort_order: Optional[str] = "desc"


class AdminUserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    institution: Optional[str] = None
    role: str = "attendee"
    research_interests: Optional[List[str]] = []

    @field_validator("password")
    @classmethod
    def password_strength(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Must contain an uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Must contain a number")
        return v

    @field_validator("role")
    @classmethod
    def valid_role(cls, v):
        valid = ["attendee", "speaker", "reviewer", "session_chair",
                 "review_chair", "moderator", "admin", "super_admin"]
        if v not in valid:
            raise ValueError(f"Invalid role: {v}")
        return v


class AdminUserUpdate(BaseModel):
    full_name: Optional[str] = None
    institution: Optional[str] = None
    bio: Optional[str] = None
    research_interests: Optional[List[str]] = None
    orcid_id: Optional[str] = None


class AdminRoleChange(BaseModel):
    role: str
    reason: Optional[str] = None

    @field_validator("role")
    @classmethod
    def valid_role(cls, v):
        valid = ["attendee", "speaker", "reviewer", "session_chair",
                 "review_chair", "moderator", "admin", "super_admin"]
        if v not in valid:
            raise ValueError(f"Invalid role: {v}")
        return v


class AdminUserSuspend(BaseModel):
    reason: str
    duration_hours: Optional[int] = None  # None = indefinite


class AdminBulkAction(BaseModel):
    user_ids: List[int]
    action: str  # suspend, activate, delete, role_change
    role: Optional[str] = None
    reason: Optional[str] = None


# ─── Responses ────────────────────────────────────────────────────
class AdminUserResponse(BaseModel):
    id: int
    email: str
    full_name: str
    institution: Optional[str] = None
    role: str
    bio: Optional[str] = None
    research_interests: Optional[List[str]] = []
    orcid_id: Optional[str] = None
    is_active: bool
    is_verified: bool
    two_factor_enabled: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    last_login: Optional[datetime] = None

    class Config:
        from_attributes = True


class AdminUserListResponse(BaseModel):
    users: List[AdminUserResponse]
    total: int
    page: int
    per_page: int
    pages: int


class AuditLogResponse(BaseModel):
    id: int
    actor_email: str
    actor_role: str
    action: str
    severity: str
    description: str
    target_email: Optional[str] = None
    target_type: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    ip_hash: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AuditLogListResponse(BaseModel):
    logs: List[AuditLogResponse]
    total: int
    page: int
    per_page: int


class DashboardStatsResponse(BaseModel):
    total_users: int
    active_users: int
    suspended_users: int
    new_today: int
    new_this_week: int
    roles_breakdown: dict
    recent_signups: List[AdminUserResponse]
    recent_activity: List[AuditLogResponse]
