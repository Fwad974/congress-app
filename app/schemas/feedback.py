"""
Feedback & Ratings schemas.
"""
from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, field_validator


def _stars(v):
    if v is None:
        return v
    if not (1 <= int(v) <= 5):
        raise ValueError("Rating must be 1–5")
    return int(v)


class RatingUpsert(BaseModel):
    stars: int
    comment: Optional[str] = None
    speaker_feedback: Optional[str] = None

    @field_validator("stars")
    @classmethod
    def v_stars(cls, v):
        v = _stars(v)
        if v is None:
            raise ValueError("Rating must be 1–5")
        return v

    @field_validator("comment", "speaker_feedback")
    @classmethod
    def v_text(cls, v):
        return (v or "").strip()[:2000] or None


class RatingResponse(BaseModel):
    session_id: int
    stars: Optional[int] = None
    comment: Optional[str] = None
    speaker_feedback: Optional[str] = None
    rated: bool = False
    updated_at: Optional[datetime] = None


class SessionSummary(BaseModel):
    session_id: int
    title: str
    average: float
    count: int
    distribution: Dict[int, int]          # {1: n, … 5: n}
    comments: List[str] = []              # anonymized session comments
    speaker_feedback: Optional[List[str]] = None   # organizers only


class MyRating(BaseModel):
    session_id: int
    title: str
    stars: int
    comment: Optional[str] = None
    updated_at: datetime


class SurveyCreate(BaseModel):
    overall: int
    organization: Optional[int] = None
    venue: Optional[int] = None
    would_recommend: Optional[bool] = None
    comment: Optional[str] = None

    @field_validator("overall")
    @classmethod
    def v_overall(cls, v):
        v = _stars(v)
        if v is None:
            raise ValueError("Overall rating is required (1–5)")
        return v

    @field_validator("organization", "venue")
    @classmethod
    def v_dims(cls, v):
        return _stars(v)

    @field_validator("comment")
    @classmethod
    def v_comment(cls, v):
        return (v or "").strip()[:4000] or None


class SurveyResponseOut(BaseModel):
    submitted: bool = False
    overall: Optional[int] = None
    organization: Optional[int] = None
    venue: Optional[int] = None
    would_recommend: Optional[bool] = None
    comment: Optional[str] = None
    created_at: Optional[datetime] = None


class SurveyAggregate(BaseModel):
    responses: int
    avg_overall: float
    avg_organization: float
    avg_venue: float
    recommend_pct: float                  # 0..100
    comments: List[str] = []


class SentimentDashboard(BaseModel):
    total_ratings: int
    overall_average: float
    distribution: Dict[int, int]
    rated_sessions: int
    top_sessions: List[SessionSummary] = []       # best average (min count)
    low_sessions: List[SessionSummary] = []       # needs attention
    recent_comments: List[str] = []
    survey: SurveyAggregate
    server_time: str


class SpeakerDigest(BaseModel):
    sessions: List[SessionSummary]        # anonymized; no speaker_feedback
    overall_average: float
    total_ratings: int
