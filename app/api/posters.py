"""
Poster gallery API — a browsable poster hall with voting, comments, and a
scavenger hunt.

Roles:
- **Presenter** (`speaker` / organizer): create + manage their posters,
  upload artwork.
- **Attendee** (any authenticated user): browse, vote, comment, and check in
  to posters for the scavenger hunt.
"""
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.uploads import read_capped
from app.core.database import get_db
from app.core.security import get_current_user
from app.core import poster_files
from app.models.user import User, UserRole
from app.models.poster import Poster, PosterVote, PosterComment, PosterVisit
from app.schemas.poster import (
    PosterCreate, PosterUpdate, PosterResponse, PosterListResponse,
    CommentCreate, CommentResponse, HuntCheckin, HuntStatus,
)

router = APIRouter()

CREATOR_ROLES = {UserRole.speaker, UserRole.session_chair, UserRole.review_chair,
                 UserRole.admin, UserRole.super_admin}
ADMIN_ROLES = {UserRole.admin, UserRole.super_admin}


def _can_create(user: User) -> bool:
    return user.role in CREATOR_ROLES


def _is_admin(user: User) -> bool:
    return user.role in ADMIN_ROLES


def _can_edit(poster: Poster, user: User) -> bool:
    return (_is_admin(user) or poster.presenter_id == user.id
            or poster.created_by == user.id)


def _new_hunt_code(db: Session) -> str:
    for _ in range(10):
        code = uuid.uuid4().hex[:8].upper()
        if not db.query(Poster.id).filter(Poster.hunt_code == code).first():
            return code
    return uuid.uuid4().hex[:12].upper()   # extremely unlikely fallback


def _comment_out(c: PosterComment, name: str, viewer: User, owner_id) -> CommentResponse:
    return CommentResponse(
        id=c.id, user_name=name or "Attendee", user_id=c.user_id, body=c.body,
        is_mine=(c.user_id == viewer.id),
        can_delete=(c.user_id == viewer.id or _is_admin(viewer) or owner_id == viewer.id),
        created_at=c.created_at,
    )


def _bulk_stats(db: Session, posters: List[Poster], viewer: User) -> dict:
    """Vote/comment counts + this viewer's votes/visits + presenter names for a
    whole list, in a handful of grouped queries instead of ~5 per poster."""
    ids = [p.id for p in posters]
    if not ids:
        return {"votes": {}, "comments": {}, "my_votes": set(), "my_visits": set(), "names": {}}
    votes = dict(db.query(PosterVote.poster_id, func.count(PosterVote.id))
                 .filter(PosterVote.poster_id.in_(ids)).group_by(PosterVote.poster_id).all())
    comments = dict(db.query(PosterComment.poster_id, func.count(PosterComment.id))
                    .filter(PosterComment.poster_id.in_(ids)).group_by(PosterComment.poster_id).all())
    my_votes = {pid for (pid,) in db.query(PosterVote.poster_id).filter(
        PosterVote.poster_id.in_(ids), PosterVote.user_id == viewer.id).all()}
    my_visits = {pid for (pid,) in db.query(PosterVisit.poster_id).filter(
        PosterVisit.poster_id.in_(ids), PosterVisit.user_id == viewer.id).all()}
    pres_ids = {p.presenter_id for p in posters if p.presenter_id}
    names = {u.id: u.full_name for u in db.query(User).filter(
        User.id.in_(pres_ids or [0])).all()} if pres_ids else {}
    return {"votes": votes, "comments": comments, "my_votes": my_votes,
            "my_visits": my_visits, "names": names}


def _serialize(db: Session, p: Poster, viewer: User, with_comments: bool = False,
               stats: dict = None) -> PosterResponse:
    if stats is not None:
        vote_count = int(stats["votes"].get(p.id, 0))
        comment_count = int(stats["comments"].get(p.id, 0))
        my_vote = p.id in stats["my_votes"]
        visited = p.id in stats["my_visits"]
        presenter_name = stats["names"].get(p.presenter_id) if p.presenter_id else None
    else:
        vote_count = db.query(func.count(PosterVote.id)).filter(
            PosterVote.poster_id == p.id).scalar() or 0
        my_vote = db.query(PosterVote.id).filter(
            PosterVote.poster_id == p.id, PosterVote.user_id == viewer.id).first() is not None
        comment_count = db.query(func.count(PosterComment.id)).filter(
            PosterComment.poster_id == p.id).scalar() or 0
        visited = db.query(PosterVisit.id).filter(
            PosterVisit.poster_id == p.id, PosterVisit.user_id == viewer.id).first() is not None
        presenter_name = (db.query(User.full_name).filter(User.id == p.presenter_id).scalar()
                          if p.presenter_id else None)
    can_edit = _can_edit(p, viewer)

    comments: List[CommentResponse] = []
    if with_comments:
        rows = db.query(PosterComment).filter(
            PosterComment.poster_id == p.id).order_by(PosterComment.created_at).all()
        names = {u.id: u.full_name for u in db.query(User).filter(
            User.id.in_([c.user_id for c in rows] or [0])).all()}
        owner_id = p.presenter_id or p.created_by
        comments = [_comment_out(c, names.get(c.user_id), viewer, owner_id) for c in rows]

    return PosterResponse(
        id=p.id, title=p.title, authors=p.authors, category=p.category,
        abstract=p.abstract, board_number=p.board_number, external_url=p.external_url,
        image_name=p.image_name, has_image=bool(p.stored_image),
        hunt_code=p.hunt_code if can_edit else None, status=p.status,
        presenter_name=presenter_name, vote_count=vote_count, my_vote=my_vote,
        comment_count=comment_count, visited=visited,
        is_mine=(p.presenter_id == viewer.id or p.created_by == viewer.id),
        can_edit=can_edit, created_at=p.created_at, updated_at=p.updated_at,
        comments=comments,
    )


def _get(db: Session, poster_id: int) -> Poster:
    p = db.query(Poster).filter(Poster.id == poster_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Poster not found")
    return p


# ─── Gallery ─────────────────────────────────────────────────────
@router.get("", response_model=PosterListResponse)
@router.get("/", response_model=PosterListResponse)
def list_posters(
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort: str = Query("votes"),   # votes | recent | board
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(Poster)
    if category:
        q = q.filter(Poster.category == category)
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(Poster.title.ilike(like) | Poster.authors.ilike(like)
                     | Poster.abstract.ilike(like))
    posters = q.all()
    # Upload-approval: hide non-approved posters from everyone except the owner
    # and moderators (owners keep seeing their own, badged "pending").
    from app.core.admin_security import is_moderator
    mod = is_moderator(user)
    posters = [p for p in posters if p.status == "approved" or mod
               or p.presenter_id == user.id or p.created_by == user.id]
    stats = _bulk_stats(db, posters, user)
    out = [_serialize(db, p, user, stats=stats) for p in posters]
    if sort == "recent":
        out.sort(key=lambda r: r.created_at, reverse=True)
    elif sort == "board":
        out.sort(key=lambda r: (r.board_number or "~", r.title))
    else:  # votes (default) — most-voted first, then newest
        out.sort(key=lambda r: (r.vote_count, r.created_at), reverse=True)
    return PosterListResponse(posters=out, total=len(out), can_create=_can_create(user))


@router.post("", response_model=PosterResponse, status_code=201)
@router.post("/", response_model=PosterResponse, status_code=201)
def create_poster(req: PosterCreate, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    if not _can_create(user):
        raise HTTPException(status_code=403,
                            detail="Only speakers and organizers can add posters")
    from app.core.moderation_service import ensure_can_post
    ensure_can_post(user)
    # Upload-approval workflow: when required, non-organizer posters wait for a
    # moderator; organizer uploads (and everything when approval is off) go live.
    approval_on = get_settings().POSTER_APPROVAL_REQUIRED
    status = "approved" if (_is_admin(user) or not approval_on) else "pending"
    poster = Poster(
        title=req.title, authors=req.authors, category=req.category,
        abstract=req.abstract, board_number=req.board_number,
        external_url=req.external_url, hunt_code=_new_hunt_code(db),
        presenter_id=user.id, created_by=user.id, status=status,
    )
    db.add(poster)
    db.commit()
    db.refresh(poster)
    return _serialize(db, poster, user, with_comments=True)


@router.get("/mine", response_model=PosterListResponse)
def my_posters(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    posters = db.query(Poster).filter(
        (Poster.presenter_id == user.id) | (Poster.created_by == user.id)
    ).order_by(Poster.created_at.desc()).all()
    stats = _bulk_stats(db, posters, user)
    return PosterListResponse(posters=[_serialize(db, p, user, stats=stats) for p in posters],
                              total=len(posters), can_create=_can_create(user))


# ─── Scavenger hunt ──────────────────────────────────────────────
@router.get("/hunt", response_model=HuntStatus)
def hunt_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _hunt_status(db, user)


def _hunt_status(db: Session, user: User) -> HuntStatus:
    goal = get_settings().POSTER_HUNT_GOAL
    ids = [pid for (pid,) in db.query(PosterVisit.poster_id).filter(
        PosterVisit.user_id == user.id).all()]
    # Only approved posters are visitable, so they alone form the hunt total.
    total = db.query(func.count(Poster.id)).filter(
        Poster.status == "approved").scalar() or 0
    return HuntStatus(visited=len(ids), goal=goal, total_posters=total,
                      complete=len(ids) >= goal, visited_poster_ids=ids)


@router.post("/hunt", response_model=HuntStatus)
def hunt_checkin(req: HuntCheckin, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    poster = db.query(Poster).filter(Poster.hunt_code == req.code).first()
    if not poster:
        raise HTTPException(status_code=404, detail="No poster matches that code")
    _visit(db, poster.id, user.id)
    return _hunt_status(db, user)


def _visit(db: Session, poster_id: int, user_id: int) -> None:
    exists = db.query(PosterVisit.id).filter(
        PosterVisit.poster_id == poster_id, PosterVisit.user_id == user_id).first()
    if not exists:
        db.add(PosterVisit(poster_id=poster_id, user_id=user_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()   # concurrent first-visit; unique constraint held


# ─── Single poster ───────────────────────────────────────────────
@router.get("/{poster_id}", response_model=PosterResponse)
def get_poster(poster_id: int, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    poster = _get(db, poster_id)
    # A not-yet-approved poster is visible only to its owner and moderators.
    from app.core.admin_security import is_moderator
    if poster.status != "approved" and not (_can_edit(poster, user) or is_moderator(user)):
        raise HTTPException(status_code=404, detail="Poster not found")
    return _serialize(db, poster, user, with_comments=True)


@router.put("/{poster_id}", response_model=PosterResponse)
def update_poster(poster_id: int, req: PosterUpdate, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    poster = _get(db, poster_id)
    if not _can_edit(poster, user):
        raise HTTPException(status_code=403, detail="You can't edit this poster")
    for f in ("title", "authors", "category", "abstract", "board_number", "external_url"):
        val = getattr(req, f)
        if val is not None:
            setattr(poster, f, val.strip() if isinstance(val, str) else val)
    poster.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(poster)
    return _serialize(db, poster, user, with_comments=True)


@router.delete("/{poster_id}", status_code=204)
def delete_poster(poster_id: int, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    poster = _get(db, poster_id)
    if not _can_edit(poster, user):
        raise HTTPException(status_code=403, detail="You can't delete this poster")
    stored = poster.stored_image
    db.delete(poster)   # votes/comments/visits cascade
    db.commit()
    if stored:
        poster_files.delete_file(stored)
    return None


# ─── Voting ──────────────────────────────────────────────────────
@router.post("/{poster_id}/vote", response_model=PosterResponse)
def vote_poster(poster_id: int, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    poster = _get(db, poster_id)
    exists = db.query(PosterVote).filter(
        PosterVote.poster_id == poster_id, PosterVote.user_id == user.id).first()
    if not exists:
        db.add(PosterVote(poster_id=poster_id, user_id=user.id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()   # concurrent double-vote; unique constraint held
    return _serialize(db, poster, user)


@router.delete("/{poster_id}/vote", response_model=PosterResponse)
def unvote_poster(poster_id: int, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    poster = _get(db, poster_id)
    db.query(PosterVote).filter(
        PosterVote.poster_id == poster_id, PosterVote.user_id == user.id).delete()
    db.commit()
    return _serialize(db, poster, user)


# ─── Comments ────────────────────────────────────────────────────
@router.post("/{poster_id}/comments", response_model=PosterResponse, status_code=201)
def add_comment(poster_id: int, req: CommentCreate, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    poster = _get(db, poster_id)
    from app.core.moderation_service import ensure_can_post, auto_flag
    ensure_can_post(user)
    c = PosterComment(poster_id=poster_id, user_id=user.id, body=req.body)
    db.add(c)
    db.commit()
    db.refresh(c)
    auto_flag(db, "poster_comment", c.id, c.body)
    return _serialize(db, poster, user, with_comments=True)


@router.delete("/{poster_id}/comments/{comment_id}", response_model=PosterResponse)
def delete_comment(poster_id: int, comment_id: int, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    poster = _get(db, poster_id)
    c = db.query(PosterComment).filter(
        PosterComment.id == comment_id, PosterComment.poster_id == poster_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Comment not found")
    from app.core.admin_security import is_moderator
    owner_id = poster.presenter_id or poster.created_by
    if not (c.user_id == user.id or is_moderator(user) or owner_id == user.id):
        raise HTTPException(status_code=403, detail="You can't delete this comment")
    db.delete(c)
    db.commit()
    return _serialize(db, poster, user, with_comments=True)


# Note: there is deliberately no id-based "visit" endpoint. Scavenger-hunt
# check-in requires the poster's secret hunt_code (via POST /hunt or a scanned
# QR) so a visit can't be faked by iterating poster ids from the gallery.


def checkin_url(request: Request, hunt_code: str) -> str:
    """Public URL the printed QR encodes: opens the gallery and checks in."""
    base = (get_settings().OAUTH_REDIRECT_BASE or str(request.base_url)).rstrip("/")
    return f"{base}/posters?checkin={hunt_code}"


@router.get("/{poster_id}/qr")
def poster_qr(poster_id: int, request: Request, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    """SVG QR for the scavenger-hunt check-in — owner/admin only (it encodes
    the hunt code, which is a secret until printed on the physical board)."""
    poster = _get(db, poster_id)
    if not _can_edit(poster, user):
        raise HTTPException(status_code=403, detail="Not allowed")
    from app.core.qr import qr_svg_document
    svg = qr_svg_document(checkin_url(request, poster.hunt_code))
    return Response(content=svg, media_type="image/svg+xml")


# ─── Poster image (upload / serve) ───────────────────────────────
@router.post("/{poster_id}/image", response_model=PosterResponse)
async def upload_image(poster_id: int, request: Request, file: UploadFile = File(...),
                       db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    poster = _get(db, poster_id)
    if not _can_edit(poster, user):
        raise HTTPException(status_code=403, detail="You can't edit this poster")
    if not poster_files.is_allowed(file.filename or ""):
        raise HTTPException(status_code=422,
                            detail=f"Only {poster_files.EXT_LABEL} files are allowed")
    data = await read_capped(file, poster_files.max_bytes(), request)
    if not data:
        raise HTTPException(status_code=422, detail="The file is empty")
    old = poster.stored_image
    poster.stored_image = poster_files.save_bytes(data, file.filename)
    poster.image_name = (file.filename or "poster")[:255]
    poster.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(poster)
    if old:
        poster_files.delete_file(old)
    return _serialize(db, poster, user)


@router.get("/{poster_id}/image")
def get_image(poster_id: int, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    from app.core.admin_security import is_moderator
    poster = _get(db, poster_id)
    # Same approval gate as the detail endpoint: a pending/rejected poster's
    # image is visible only to its owner and moderators, never by id-enumeration.
    if poster.status != "approved" and not (_can_edit(poster, user) or is_moderator(user)):
        raise HTTPException(status_code=404, detail="Poster not found")
    if not poster.stored_image:
        raise HTTPException(status_code=404, detail="No image uploaded")
    path = poster_files.path_for(poster.stored_image)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Image is missing")
    return FileResponse(path, filename=poster.image_name or "poster")
