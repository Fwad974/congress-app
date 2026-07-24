"""
Sponsor & Exhibitor Portal schemas.
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator

from app.models.sponsor import SPONSOR_TIERS


def _clean_url(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    v = v.strip()
    if v and not (v.startswith("http://") or v.startswith("https://")):
        raise ValueError("Link must start with http:// or https://")
    return v[:500]


class TeamMember(BaseModel):
    name: str
    title: Optional[str] = None
    bio: Optional[str] = None

    @field_validator("name")
    @classmethod
    def v_name(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Team member name is required")
        return v[:120]


class SponsorCreate(BaseModel):
    name: str
    tier: str = "silver"
    tagline: Optional[str] = None
    website_url: Optional[str] = None
    about: Optional[str] = None
    video_url: Optional[str] = None
    brochure_url: Optional[str] = None
    booth_number: Optional[str] = None
    team: Optional[List[TeamMember]] = None
    is_active: bool = True
    display_order: int = 0

    @field_validator("name")
    @classmethod
    def v_name(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Sponsor name is required")
        return v[:200]

    @field_validator("tier")
    @classmethod
    def v_tier(cls, v):
        v = (v or "").strip().lower()
        if v not in SPONSOR_TIERS:
            raise ValueError(f"Tier must be one of: {', '.join(SPONSOR_TIERS)}")
        return v

    @field_validator("website_url", "video_url", "brochure_url")
    @classmethod
    def v_urls(cls, v):
        return _clean_url(v)


class SponsorUpdate(BaseModel):
    name: Optional[str] = None
    tier: Optional[str] = None
    tagline: Optional[str] = None
    website_url: Optional[str] = None
    about: Optional[str] = None
    video_url: Optional[str] = None
    brochure_url: Optional[str] = None
    booth_number: Optional[str] = None
    team: Optional[List[TeamMember]] = None
    is_active: Optional[bool] = None
    display_order: Optional[int] = None

    @field_validator("tier")
    @classmethod
    def v_tier(cls, v):
        if v is None:
            return v
        v = v.strip().lower()
        if v not in SPONSOR_TIERS:
            raise ValueError(f"Tier must be one of: {', '.join(SPONSOR_TIERS)}")
        return v

    @field_validator("website_url", "video_url", "brochure_url")
    @classmethod
    def v_urls(cls, v):
        return _clean_url(v)


class SponsorResponse(BaseModel):
    id: int
    name: str
    tier: str
    tagline: Optional[str] = None
    website_url: Optional[str] = None
    has_logo: bool = False
    booth_number: Optional[str] = None
    is_active: bool = True
    display_order: int = 0
    # Booth detail (populated on the single-sponsor view)
    about: Optional[str] = None
    video_url: Optional[str] = None
    brochure_url: Optional[str] = None
    team: List[TeamMember] = []
    # Attendee context
    lead_submitted: bool = False
    # Organizer context (analytics inline)
    can_manage: bool = False
    unique_visitors: Optional[int] = None
    total_visits: Optional[int] = None
    lead_count: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class SponsorListResponse(BaseModel):
    sponsors: List[SponsorResponse]
    total: int
    can_manage: bool = False
    tiers: List[str] = SPONSOR_TIERS


class LeadCreate(BaseModel):
    consent: bool = False
    message: Optional[str] = None

    @field_validator("message")
    @classmethod
    def v_message(cls, v):
        if v is None:
            return v
        return v.strip()[:2000] or None


class LeadResponse(BaseModel):
    id: int
    sponsor_id: int
    user_id: int
    name: str
    email: str
    institution: Optional[str] = None
    message: Optional[str] = None
    created_at: datetime


class SponsorAnalytics(BaseModel):
    sponsor_id: int
    name: str
    tier: str
    unique_visitors: int
    total_visits: int
    leads: int
    conversion_rate: float   # leads / unique_visitors, 0..1


class AnalyticsResponse(BaseModel):
    sponsors: List[SponsorAnalytics]
    totals: dict
