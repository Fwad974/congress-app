"""
Poster gallery schemas.
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator


def _required(v, name, maxlen):
    v = (v or "").strip()
    if not v:
        raise ValueError(f"{name} is required")
    return v[:maxlen]


def _http_url(v):
    if v is None:
        return v
    v = v.strip()
    if v and not (v.startswith("http://") or v.startswith("https://")):
        raise ValueError("Link must start with http:// or https://")
    return v[:500]


class PosterCreate(BaseModel):
    title: str
    authors: str
    category: Optional[str] = None
    abstract: Optional[str] = None
    board_number: Optional[str] = None
    external_url: Optional[str] = None

    @field_validator("title")
    @classmethod
    def v_title(cls, v):
        return _required(v, "Title", 300)

    @field_validator("authors")
    @classmethod
    def v_authors(cls, v):
        return _required(v, "Authors", 1000)

    @field_validator("external_url")
    @classmethod
    def v_url(cls, v):
        return _http_url(v)


class PosterUpdate(BaseModel):
    title: Optional[str] = None
    authors: Optional[str] = None
    category: Optional[str] = None
    abstract: Optional[str] = None
    board_number: Optional[str] = None
    external_url: Optional[str] = None

    @field_validator("external_url")
    @classmethod
    def v_url(cls, v):
        return _http_url(v)


class CommentCreate(BaseModel):
    body: str

    @field_validator("body")
    @classmethod
    def v_body(cls, v):
        return _required(v, "Comment", 2000)


class CommentResponse(BaseModel):
    id: int
    user_name: str
    user_id: int
    body: str
    is_mine: bool = False
    can_delete: bool = False
    created_at: datetime


class PosterResponse(BaseModel):
    id: int
    title: str
    authors: str
    category: Optional[str] = None
    abstract: Optional[str] = None
    board_number: Optional[str] = None
    external_url: Optional[str] = None
    image_name: Optional[str] = None
    has_image: bool = False
    hunt_code: Optional[str] = None        # only exposed to owner/organizer
    status: str = "approved"               # approved | pending | rejected
    presenter_name: Optional[str] = None
    vote_count: int = 0
    my_vote: bool = False
    comment_count: int = 0
    visited: bool = False
    is_mine: bool = False                  # viewer is the presenter/creator
    can_edit: bool = False
    created_at: datetime
    updated_at: datetime
    comments: List[CommentResponse] = []


class PosterListResponse(BaseModel):
    posters: List[PosterResponse]
    total: int
    can_create: bool = False


class HuntCheckin(BaseModel):
    code: str

    @field_validator("code")
    @classmethod
    def v_code(cls, v):
        return _required(v, "Code", 12).upper()


class HuntStatus(BaseModel):
    visited: int
    goal: int
    total_posters: int
    complete: bool
    visited_poster_ids: List[int] = []
