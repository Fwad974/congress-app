"""
Live Q&A schemas.
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator

MAX_QUESTION_LEN = 500


class QuestionCreate(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def clean_text(cls, v):
        if v is None:
            raise ValueError("Question text is required")
        v = v.strip()
        if not v:
            raise ValueError("Question cannot be empty")
        if len(v) > MAX_QUESTION_LEN:
            raise ValueError(f"Question too long (max {MAX_QUESTION_LEN} characters)")
        return v


class QuestionStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def valid_status(cls, v):
        if v not in {"open", "answered", "hidden"}:
            raise ValueError("Invalid status")
        return v


class QuestionResponse(BaseModel):
    id: int
    session_id: int
    text: str
    status: str
    upvotes: int
    author_id: int
    author_name: str
    is_mine: bool = False
    has_upvoted: bool = False
    created_at: datetime


class QuestionListResponse(BaseModel):
    questions: List[QuestionResponse]
    total: int
    can_moderate: bool = False
