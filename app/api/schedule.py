"""
Schedule API — Public listing + admin CRUD for the congress program.

- Anyone authenticated can list/view schedule items and toggle their own bookmarks.
- Only admins (admin/super_admin) can create, update, or delete schedule items.
- All admin mutations are written to the audit log.
"""
from datetime import datetime, timezone
from typing import Dict, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Request, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import asc

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.admin_security import require_admin, is_live_moderator
from app.core.audit_service import log_action
from app.core.notification_service import notify_bookmarkers


def _notify_assignment(db, item, user_id, role_word):
    """Tell a user they've been assigned to present or chair a session."""
    if not user_id:
        return
    from app.models.notification import UserNotification
    db.add(UserNotification(
        user_id=user_id, schedule_item_id=item.id, kind="assignment",
        title=f"You're {role_word} for a session",
        body=f"You've been assigned to {role_word} \"{item.title}\".",
    ))
from app.core.push_service import deliver_push
from app.models.user import User, UserRole
from app.models.schedule import ScheduleItem, ScheduleType, ScheduleBookmark
from app.models.audit_log import AuditAction
from app.schemas.schedule import (
    ScheduleItemCreate, ScheduleItemUpdate, PresentationUpdate,
    ScheduleItemResponse, ScheduleListResponse,
    _coerce_extra,
)

router = APIRouter()


def _resolve_speaker(db: Session, email: Optional[str]) -> Optional[User]:
    """Resolve a presenter email to a user. "" / None means no presenter."""
    email = (email or "").strip()
    if not email:
        return None
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=400, detail=f"No user with email {email}")
    return user


def _speaker_map(db: Session, items) -> Dict[int, User]:
    """User rows keyed by id for every speaker AND chair referenced by items."""
    ids = {i.speaker_id for i in items if i.speaker_id}
    ids |= {i.chair_id for i in items if i.chair_id}
    if not ids:
        return {}
    return {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()}


def _bookmarked_ids(db: Session, user_id: int) -> Set[int]:
    rows = db.query(ScheduleBookmark.schedule_item_id).filter(
        ScheduleBookmark.user_id == user_id
    ).all()
    return {r[0] for r in rows}


def _change_summary(item: ScheduleItem, old_start, old_end, old_location) -> str:
    """Human-friendly note for a bookmarked session that an admin just edited."""
    parts = []
    if item.start_time != old_start or item.end_time != old_end:
        parts.append(f"rescheduled to {item.start_time.strftime('%b %d, %H:%M')}")
    if item.location != old_location and item.location:
        parts.append(f"moved to {item.location}")
    if parts:
        return "This session has been " + " and ".join(parts) + "."
    return "Details for this session have been updated."


def _serialize(item: ScheduleItem, bookmarked: bool = False,
               current_user: Optional[User] = None,
               speaker_user: Optional[User] = None,
               chair_user: Optional[User] = None) -> ScheduleItemResponse:
    """Shape a row for the response, normalizing extra/None to {}."""
    extra = item.extra or {}
    return ScheduleItemResponse(
        id=item.id,
        title=item.title,
        description=item.description,
        type=item.type.value,
        location=item.location,
        start_time=item.start_time,
        end_time=item.end_time,
        extra=extra,
        is_bookmarked=bookmarked,
        speaker_id=item.speaker_id,
        speaker_email=speaker_user.email if speaker_user else None,
        speaker_name=(speaker_user.full_name if speaker_user else None) or extra.get("speaker"),
        is_presenter=bool(current_user and item.speaker_id
                          and current_user.id == item.speaker_id),
        chair_id=item.chair_id,
        chair_email=chair_user.email if chair_user else None,
        chair_name=chair_user.full_name if chair_user else None,
        is_chair=bool(current_user and item.chair_id
                      and current_user.id == item.chair_id),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


# ─── Public (any authenticated user) ──────────────────────────────
@router.get("", response_model=ScheduleListResponse)
@router.get("/", response_model=ScheduleListResponse)
def list_schedule(
    type: Optional[str] = Query(None),
    bookmarked: bool = Query(False),
    presenter: Optional[str] = Query(None),  # "me" = only sessions I present
    chair: Optional[str] = Query(None),      # "me" = only sessions I chair
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(ScheduleItem)
    if type:
        try:
            q = q.filter(ScheduleItem.type == ScheduleType(type))
        except ValueError:
            pass

    if presenter == "me":
        q = q.filter(ScheduleItem.speaker_id == user.id)
    if chair == "me":
        q = q.filter(ScheduleItem.chair_id == user.id)

    bookmarks = _bookmarked_ids(db, user.id)
    if bookmarked:
        if not bookmarks:
            return ScheduleListResponse(items=[], total=0)
        q = q.filter(ScheduleItem.id.in_(bookmarks))

    items = q.order_by(asc(ScheduleItem.start_time)).all()
    users = _speaker_map(db, items)
    return ScheduleListResponse(
        items=[_serialize(i, bookmarked=i.id in bookmarks, current_user=user,
                          speaker_user=users.get(i.speaker_id),
                          chair_user=users.get(i.chair_id)) for i in items],
        total=len(items),
    )


@router.get("/{item_id}", response_model=ScheduleItemResponse)
def get_schedule_item(
    item_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = db.query(ScheduleItem).filter(ScheduleItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Schedule item not found")
    bm = db.query(ScheduleBookmark).filter(
        ScheduleBookmark.user_id == user.id,
        ScheduleBookmark.schedule_item_id == item_id,
    ).first() is not None
    users = _speaker_map(db, [item])
    return _serialize(item, bookmarked=bm, current_user=user,
                      speaker_user=users.get(item.speaker_id),
                      chair_user=users.get(item.chair_id))


# ─── Bookmarks (any authenticated user) ──────────────────────────
@router.post("/{item_id}/bookmark")
def add_bookmark(
    item_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = db.query(ScheduleItem).filter(ScheduleItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Schedule item not found")

    existing = db.query(ScheduleBookmark).filter(
        ScheduleBookmark.user_id == user.id,
        ScheduleBookmark.schedule_item_id == item_id,
    ).first()
    if existing:
        return {"message": "Already bookmarked", "is_bookmarked": True}

    bm = ScheduleBookmark(user_id=user.id, schedule_item_id=item_id)
    db.add(bm)
    db.commit()
    return {"message": "Bookmarked", "is_bookmarked": True}


@router.delete("/{item_id}/bookmark")
def remove_bookmark(
    item_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    deleted = db.query(ScheduleBookmark).filter(
        ScheduleBookmark.user_id == user.id,
        ScheduleBookmark.schedule_item_id == item_id,
    ).delete(synchronize_session=False)
    db.commit()
    return {"message": "Bookmark removed" if deleted else "Not bookmarked",
            "is_bookmarked": False}


# ─── Presenter self-service ──────────────────────────────────────
@router.put("/{item_id}/presentation", response_model=ScheduleItemResponse)
def update_presentation(
    item_id: int,
    req: PresentationUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """A session's own presenter (or an admin) edits its abstract, description,
    and slides/materials link — never the time, room, or title."""
    item = db.query(ScheduleItem).filter(ScheduleItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Schedule item not found")

    is_owner = item.speaker_id is not None and item.speaker_id == user.id
    is_admin = user.role in (UserRole.admin, UserRole.super_admin)
    if not is_owner and not is_admin:
        raise HTTPException(status_code=403, detail="Only the session's presenter can edit this")

    extra = dict(item.extra or {})
    if req.abstract is not None:
        a = req.abstract.strip()[:5000]
        if a:
            extra["abstract"] = a
        else:
            extra.pop("abstract", None)
    if req.materials_url is not None:
        if req.materials_url:
            extra["materials_url"] = req.materials_url
        else:
            extra.pop("materials_url", None)
    item.extra = _coerce_extra(item.type.value, extra)
    if req.description is not None:
        item.description = req.description
    item.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(item)

    bm = db.query(ScheduleBookmark).filter(
        ScheduleBookmark.user_id == user.id,
        ScheduleBookmark.schedule_item_id == item.id,
    ).first() is not None
    speaker = _speaker_map(db, [item]).get(item.speaker_id)
    return _serialize(item, bookmarked=bm, current_user=user, speaker_user=speaker)


# ─── Admin CRUD ───────────────────────────────────────────────────
@router.post("", response_model=ScheduleItemResponse, status_code=201)
@router.post("/", response_model=ScheduleItemResponse, status_code=201)
def create_schedule_item(
    req: ScheduleItemCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin()),
):
    speaker = _resolve_speaker(db, req.speaker_email)
    chair = _resolve_speaker(db, req.chair_email)
    item = ScheduleItem(
        title=req.title.strip(),
        description=req.description,
        type=ScheduleType(req.type),
        location=req.location,
        start_time=req.start_time,
        end_time=req.end_time,
        extra=req.extra or {},
        speaker_id=speaker.id if speaker else None,
        chair_id=chair.id if chair else None,
        created_by=admin.id,
    )
    db.add(item)
    db.flush()

    # Notify the presenter/chair they've been assigned.
    if speaker:
        _notify_assignment(db, item, speaker.id, "presenting")
    if chair:
        _notify_assignment(db, item, chair.id, "chairing")

    log_action(
        db, admin, AuditAction.schedule_create,
        f"Created schedule item '{item.title}' ({item.type.value}) at {item.start_time.isoformat()}",
        request=request, target_type="schedule", target_id=item.id,
        new_value=item.title,
    )
    db.refresh(item)
    return _serialize(item, bookmarked=False, current_user=admin,
                      speaker_user=speaker, chair_user=chair)


@router.put("/{item_id}", response_model=ScheduleItemResponse)
def update_schedule_item(
    item_id: int,
    req: ScheduleItemUpdate,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin()),
):
    item = db.query(ScheduleItem).filter(ScheduleItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Schedule item not found")

    # Snapshot the fields we care about so we can detect changes and craft a
    # meaningful "your session changed" message for bookmarkers.
    before = (
        item.title, item.description, item.type, item.location,
        item.start_time, item.end_time, dict(item.extra or {}),
    )
    old_start, old_end, old_location = item.start_time, item.end_time, item.location

    if req.title is not None:
        title = req.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Title cannot be empty")
        item.title = title
    if req.description is not None:
        item.description = req.description
    if req.type is not None:
        item.type = ScheduleType(req.type)
    if req.location is not None:
        item.location = req.location
    if req.start_time is not None:
        item.start_time = req.start_time
    if req.end_time is not None:
        item.end_time = req.end_time

    # A partial update can mix an aware incoming value with a naive stored one
    # (SQLite), which raises TypeError on comparison — normalise both to UTC.
    def _aware(dt):
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    if _aware(item.end_time) <= _aware(item.start_time):
        raise HTTPException(status_code=400, detail="end_time must be after start_time")

    # Coerce extras against the (possibly new) type
    if req.extra is not None or req.type is not None:
        raw_extra = req.extra if req.extra is not None else (item.extra or {})
        item.extra = _coerce_extra(item.type.value, raw_extra)

    # Reassign (or clear) the presenter/chair only when the field is sent.
    if "speaker_email" in req.model_fields_set:
        sp = _resolve_speaker(db, req.speaker_email)
        new_id = sp.id if sp else None
        if new_id and new_id != item.speaker_id:
            _notify_assignment(db, item, new_id, "presenting")
        item.speaker_id = new_id
    if "chair_email" in req.model_fields_set:
        ch = _resolve_speaker(db, req.chair_email)
        new_id = ch.id if ch else None
        if new_id and new_id != item.chair_id:
            _notify_assignment(db, item, new_id, "chairing")
        item.chair_id = new_id

    item.updated_at = datetime.now(timezone.utc)
    db.flush()

    # Any real change to a bookmarked item notifies its bookmarkers.
    after = (
        item.title, item.description, item.type, item.location,
        item.start_time, item.end_time, dict(item.extra or {}),
    )
    recipients = []
    summary = ""
    if after != before:
        summary = _change_summary(item, old_start, old_end, old_location)
        recipients = notify_bookmarkers(
            db, item, kind="updated", body=summary, exclude_user_id=admin.id,
        )

    log_action(
        db, admin, AuditAction.schedule_update,
        f"Updated schedule item #{item.id} '{item.title}'",
        request=request, target_type="schedule", target_id=item.id,
        new_value=item.title,
    )
    # Best-effort web push after the change is committed.
    if recipients:
        background.add_task(deliver_push, recipients, {
            "title": item.title, "body": summary,
            "tag": f"sched-{item.id}", "url": "/schedule",
        })
    db.refresh(item)
    bm = db.query(ScheduleBookmark).filter(
        ScheduleBookmark.user_id == admin.id,
        ScheduleBookmark.schedule_item_id == item.id,
    ).first() is not None
    users = _speaker_map(db, [item])
    return _serialize(item, bookmarked=bm, current_user=admin,
                      speaker_user=users.get(item.speaker_id),
                      chair_user=users.get(item.chair_id))


@router.delete("/{item_id}")
def delete_schedule_item(
    item_id: int,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin()),
):
    item = db.query(ScheduleItem).filter(ScheduleItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Schedule item not found")

    title = item.title
    cancel_msg = "This session has been cancelled."

    # Tell bookmarkers their session is cancelled before we drop the bookmarks.
    recipients = notify_bookmarkers(
        db, item, kind="cancelled", body=cancel_msg, exclude_user_id=admin.id,
    )

    log_action(
        db, admin, AuditAction.schedule_delete,
        f"Deleted schedule item #{item.id} '{title}'",
        request=request, target_type="schedule", target_id=item.id,
        old_value=title,
    )

    # Drop dependent bookmarks first (SQLite doesn't enforce FK cascades by default).
    db.query(ScheduleBookmark).filter(
        ScheduleBookmark.schedule_item_id == item_id
    ).delete(synchronize_session=False)
    db.delete(item)
    db.commit()

    if recipients:
        background.add_task(deliver_push, recipients, {
            "title": title, "body": cancel_msg,
            "tag": f"sched-{item_id}", "url": "/schedule",
        })
    return {"message": f"Schedule item '{title}' deleted"}
