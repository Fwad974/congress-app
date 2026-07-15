"""
Live poll schemas.
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator

MAX_QUESTION_LEN = 300
MAX_OPTION_LEN = 120
MAX_OPTIONS = 10
MAX_WORD_LEN = 60
WORDCLOUD_MAX_PER_USER = 5


class PollCreate(BaseModel):
    question: str
    type: str = "single"
    options: List[str] = []

    @field_validator("question")
    @classmethod
    def clean_question(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Question is required")
        if len(v) > MAX_QUESTION_LEN:
            raise ValueError(f"Question too long (max {MAX_QUESTION_LEN})")
        return v

    @field_validator("type")
    @classmethod
    def valid_type(cls, v):
        if v not in {"single", "multiple", "wordcloud"}:
            raise ValueError("Invalid poll type")
        return v

    @field_validator("options")
    @classmethod
    def clean_options(cls, v):
        cleaned = []
        for o in (v or []):
            o = (o or "").strip()
            if o:
                cleaned.append(o[:MAX_OPTION_LEN])
        if len(cleaned) > MAX_OPTIONS:
            raise ValueError(f"Too many options (max {MAX_OPTIONS})")
        return cleaned


class VoteRequest(BaseModel):
    option_ids: List[int] = []
    text: Optional[str] = None


class OptionResult(BaseModel):
    id: int
    text: str
    count: int


class WordCount(BaseModel):
    text: str
    count: int


class PollResults(BaseModel):
    poll_id: int
    type: str
    total: int                          # total responses
    options: List[OptionResult] = []    # choice polls
    words: List[WordCount] = []         # word clouds


class PollResponse(BaseModel):
    id: int
    session_id: int
    question: str
    type: str
    status: str
    options: List[OptionResult] = []
    my_option_ids: List[int] = []
    my_words: List[str] = []
    can_moderate: bool = False


class PollListResponse(BaseModel):
    polls: List[PollResponse]
    can_moderate: bool = False
