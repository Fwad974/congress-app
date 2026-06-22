"""
Note schemas — user CRUD + admin moderation views.
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator

MAX_NOTE_LEN = 10000


def _clean_content(v: str) -> str:
    if v is None:
        raise ValueError("Content is required")
    v = v.strip()
    if not v:
        raise ValueError("Note cannot be empty")
    if len(v) > MAX_NOTE_LEN:
        raise ValueError(f"Note too long (max {MAX_NOTE_LEN} characters)")
    return v


class NoteCreate(BaseModel):
    content: str
    schedule_item_id: Optional[int] = None

    @field_validator("content")
    @classmethod
    def validate_content(cls, v):
        return _clean_content(v)


class NoteUpdate(BaseModel):
    content: Optional[str] = None
    schedule_item_id: Optional[int] = None

    @field_validator("content")
    @classmethod
    def validate_content(cls, v):
        if v is None:
            return v
        return _clean_content(v)


class NoteResponse(BaseModel):
    id: int
    content: str
    schedule_item_id: Optional[int] = None
    session_title: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class NoteListResponse(BaseModel):
    notes: List[NoteResponse]
    total: int


# ─── Admin moderation ────────────────────────────────────────────
class AdminNoteResponse(NoteResponse):
    user_id: int
    user_name: str
    user_email: str


class AdminNoteListResponse(BaseModel):
    notes: List[AdminNoteResponse]
    total: int
    page: int
    per_page: int
    pages: int
