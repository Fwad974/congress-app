"""
Paper submission + review schemas.
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator

VALID_DECISIONS = {"accept", "reject", "revision"}


def _required(v, name, maxlen):
    v = (v or "").strip()
    if not v:
        raise ValueError(f"{name} is required")
    return v[:maxlen]


class PaperCreate(BaseModel):
    title: str
    authors: str
    abstract: str
    category: Optional[str] = None
    file_url: Optional[str] = None

    @field_validator("title")
    @classmethod
    def v_title(cls, v):
        return _required(v, "Title", 300)

    @field_validator("authors")
    @classmethod
    def v_authors(cls, v):
        return _required(v, "Authors", 1000)

    @field_validator("abstract")
    @classmethod
    def v_abstract(cls, v):
        return _required(v, "Abstract", 8000)

    @field_validator("file_url")
    @classmethod
    def v_url(cls, v):
        if v is None:
            return v
        v = v.strip()
        if v and not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("Link must start with http:// or https://")
        return v[:500]


class PaperUpdate(BaseModel):
    """Author edits a draft or resubmits a revision."""
    title: Optional[str] = None
    authors: Optional[str] = None
    abstract: Optional[str] = None
    category: Optional[str] = None
    file_url: Optional[str] = None
    author_response: Optional[str] = None

    @field_validator("file_url")
    @classmethod
    def v_url(cls, v):
        if v is None:
            return v
        v = v.strip()
        if v and not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("Link must start with http:// or https://")
        return v[:500]


class ReviewUpsert(BaseModel):
    score: Optional[int] = None
    comments: Optional[str] = None
    submitted: bool = False

    @field_validator("score")
    @classmethod
    def v_score(cls, v):
        if v is None:
            return v
        if not (1 <= int(v) <= 5):
            raise ValueError("Score must be 1–5")
        return int(v)


class AssignRequest(BaseModel):
    reviewer_ids: List[int]
    override_coi: bool = False


class DecisionRequest(BaseModel):
    decision: str
    comment: Optional[str] = None

    @field_validator("decision")
    @classmethod
    def v_decision(cls, v):
        if v not in VALID_DECISIONS:
            raise ValueError("Decision must be accept, reject, or revision")
        return v


class ReviewResponse(BaseModel):
    id: int
    reviewer_label: str            # "You", real name (chair), or "Reviewer 2" (author)
    reviewer_id: Optional[int] = None   # only exposed to chairs
    score: Optional[int] = None
    comments: Optional[str] = None
    submitted: bool
    updated_at: datetime


class PaperResponse(BaseModel):
    id: int
    title: str
    authors: str
    category: Optional[str] = None
    abstract: str
    file_url: Optional[str] = None
    file_name: Optional[str] = None   # original name of the uploaded manuscript
    has_file: bool = False            # a manuscript is attached (download available)
    status: str
    round: int
    author_response: Optional[str] = None
    decision_comment: Optional[str] = None
    author_name: Optional[str] = None    # hidden from reviewers (blind)
    is_mine: bool = False
    can_manage: bool = False
    review_count: int = 0
    submitted_review_count: int = 0
    avg_score: Optional[float] = None
    my_review: Optional[ReviewResponse] = None   # for an assigned reviewer
    reviews: List[ReviewResponse] = []           # visible to chair, or author post-decision
    created_at: datetime
    updated_at: datetime


class PaperListResponse(BaseModel):
    papers: List[PaperResponse]
    total: int
    can_manage: bool = False


class ReviewerInfo(BaseModel):
    id: int
    full_name: str
    email: str
    institution: Optional[str] = None
    coi: bool = False        # same institution as the paper's author
    assigned: bool = False
