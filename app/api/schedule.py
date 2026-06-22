"""
Schedule API — Public listing + admin CRUD for the congress program.

- Anyone authenticated can list/view schedule items and toggle their own bookmarks.
- Only admins (admin/super_admin) can create, update, or delete schedule items.
- All admin mutations are written to the audit log.
"""
from datetime import datetime, timezone
from typing import Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import asc

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.admin_security import require_admin
from app.core.audit_service import log_action
from app.core.notification_service import notify_bookmarkers
from app.models.user import User
from app.models.schedule import ScheduleItem, ScheduleType, ScheduleBookmark
from app.models.audit_log import AuditAction
from app.schemas.schedule import (
    ScheduleItemCreate, ScheduleItemUpdate,
    ScheduleItemResponse, ScheduleListResponse,
    _coerce_extra,
)

router = APIRouter()


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


def _serialize(item: ScheduleItem, bookmarked: bool = False) -> ScheduleItemResponse:
    """Shape a row for the response, normalizing extra/None to {}."""
    return ScheduleItemResponse(
        id=item.id,
        title=item.title,
        description=item.description,
        type=item.type.value,
        location=item.location,
        start_time=item.start_time,
        end_time=item.end_time,
        extra=item.extra or {},
        is_bookmarked=bookmarked,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


# ─── Public (any authenticated user) ──────────────────────────────
@router.get("", response_model=ScheduleListResponse)
@router.get("/", response_model=ScheduleListResponse)
def list_schedule(
    type: Optional[str] = Query(None),
    bookmarked: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(ScheduleItem)
    if type:
        try:
            q = q.filter(ScheduleItem.type == ScheduleType(type))
        except ValueError:
            pass

    bookmarks = _bookmarked_ids(db, user.id)
    if bookmarked:
        if not bookmarks:
            return ScheduleListResponse(items=[], total=0)
        q = q.filter(ScheduleItem.id.in_(bookmarks))

    items = q.order_by(asc(ScheduleItem.start_time)).all()
    return ScheduleListResponse(
        items=[_serialize(i, bookmarked=i.id in bookmarks) for i in items],
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
    return _serialize(item, bookmarked=bm)


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


# ─── Admin CRUD ───────────────────────────────────────────────────
@router.post("", response_model=ScheduleItemResponse, status_code=201)
@router.post("/", response_model=ScheduleItemResponse, status_code=201)
def create_schedule_item(
    req: ScheduleItemCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin()),
):
    item = ScheduleItem(
        title=req.title.strip(),
        description=req.description,
        type=ScheduleType(req.type),
        location=req.location,
        start_time=req.start_time,
        end_time=req.end_time,
        extra=req.extra or {},
        created_by=admin.id,
    )
    db.add(item)
    db.flush()

    log_action(
        db, admin, AuditAction.schedule_create,
        f"Created schedule item '{item.title}' ({item.type.value}) at {item.start_time.isoformat()}",
        request=request, target_type="schedule", target_id=item.id,
        new_value=item.title,
    )
    db.refresh(item)
    return _serialize(item, bookmarked=False)


@router.put("/{item_id}", response_model=ScheduleItemResponse)
def update_schedule_item(
    item_id: int,
    req: ScheduleItemUpdate,
    request: Request,
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

    if item.end_time <= item.start_time:
        raise HTTPException(status_code=400, detail="end_time must be after start_time")

    # Coerce extras against the (possibly new) type
    if req.extra is not None or req.type is not None:
        raw_extra = req.extra if req.extra is not None else (item.extra or {})
        item.extra = _coerce_extra(item.type.value, raw_extra)

    item.updated_at = datetime.now(timezone.utc)
    db.flush()

    # Any real change to a bookmarked item notifies its bookmarkers.
    after = (
        item.title, item.description, item.type, item.location,
        item.start_time, item.end_time, dict(item.extra or {}),
    )
    if after != before:
        notify_bookmarkers(
            db, item, kind="updated",
            body=_change_summary(item, old_start, old_end, old_location),
            exclude_user_id=admin.id,
        )

    log_action(
        db, admin, AuditAction.schedule_update,
        f"Updated schedule item #{item.id} '{item.title}'",
        request=request, target_type="schedule", target_id=item.id,
        new_value=item.title,
    )
    db.refresh(item)
    bm = db.query(ScheduleBookmark).filter(
        ScheduleBookmark.user_id == admin.id,
        ScheduleBookmark.schedule_item_id == item.id,
    ).first() is not None
    return _serialize(item, bookmarked=bm)


@router.delete("/{item_id}")
def delete_schedule_item(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin()),
):
    item = db.query(ScheduleItem).filter(ScheduleItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Schedule item not found")

    title = item.title

    # Tell bookmarkers their session is cancelled before we drop the bookmarks.
    notify_bookmarkers(
        db, item, kind="cancelled",
        body="This session has been cancelled.",
        exclude_user_id=admin.id,
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
    return {"message": f"Schedule item '{title}' deleted"}
