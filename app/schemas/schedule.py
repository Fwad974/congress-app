"""
Schedule Schemas — Request/Response models for congress schedule items.
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator, model_validator


VALID_TYPES = ["keynote", "talk", "panel", "workshop", "poster",
               "break", "networking", "other"]


class ScheduleItemBase(BaseModel):
    title: str
    description: Optional[str] = None
    type: str = "talk"
    speaker: Optional[str] = None
    location: Optional[str] = None
    start_time: datetime
    end_time: datetime

    @field_validator("title")
    @classmethod
    def title_required(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Title is required")
        if len(v) > 255:
            raise ValueError("Title is too long")
        return v

    @field_validator("type")
    @classmethod
    def valid_type(cls, v):
        if v not in VALID_TYPES:
            raise ValueError(f"Invalid type: {v}")
        return v

    @model_validator(mode="after")
    def end_after_start(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class ScheduleItemCreate(ScheduleItemBase):
    pass


class ScheduleItemUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    type: Optional[str] = None
    speaker: Optional[str] = None
    location: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    @field_validator("type")
    @classmethod
    def valid_type(cls, v):
        if v is None:
            return v
        if v not in VALID_TYPES:
            raise ValueError(f"Invalid type: {v}")
        return v


class ScheduleItemResponse(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    type: str
    speaker: Optional[str] = None
    location: Optional[str] = None
    start_time: datetime
    end_time: datetime
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ScheduleListResponse(BaseModel):
    items: List[ScheduleItemResponse]
    total: int
