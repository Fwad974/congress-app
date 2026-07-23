"""
Networking directory — an opt-in list of attendees for finding peers.

Privacy model: nobody appears until they flip "Show me in the directory"
(``User.networking_visible``). Opting in shares name, role, institution,
research interests, bio, and email with other authenticated attendees.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User

router = APIRouter()


class DirectoryEntry(BaseModel):
    id: int
    full_name: str
    role: str
    institution: Optional[str] = None
    bio: Optional[str] = None
    research_interests: List[str] = []
    email: str
    orcid_id: Optional[str] = None
    is_me: bool = False


class DirectoryResponse(BaseModel):
    people: List[DirectoryEntry]
    total: int
    visible: bool          # the viewer's own opt-in state


class VisibilityUpdate(BaseModel):
    visible: bool


def _entry(u: User, viewer: User) -> DirectoryEntry:
    return DirectoryEntry(
        id=u.id, full_name=u.full_name, role=u.role.value,
        institution=u.institution, bio=u.bio,
        research_interests=u.research_interests or [],
        email=u.email, orcid_id=u.orcid_id, is_me=(u.id == viewer.id),
    )


@router.get("/directory", response_model=DirectoryResponse)
def directory(
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(User).filter(
        User.networking_visible == True,  # noqa: E712
        User.is_active == True,           # noqa: E712
    )
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(or_(User.full_name.ilike(like),
                         User.institution.ilike(like),
                         User.bio.ilike(like)))
    people = q.order_by(User.full_name).all()
    return DirectoryResponse(
        people=[_entry(u, user) for u in people],
        total=len(people),
        visible=bool(user.networking_visible),
    )


@router.put("/visibility", response_model=DirectoryResponse)
def set_visibility(req: VisibilityUpdate, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    db_user = db.query(User).filter(User.id == user.id).first()
    db_user.networking_visible = req.visible
    db.commit()
    return directory(search=None, db=db, user=db_user)
