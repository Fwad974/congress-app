"""
Schedule API — Public listing + admin CRUD for the congress program.

- Anyone authenticated can list/view schedule items.
- Only admins (admin/super_admin) can create, update, or delete.
- All admin mutations are written to the audit log.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import asc

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.admin_security import require_admin
from app.core.audit_service import log_action
from app.models.user import User
from app.models.schedule import ScheduleItem, ScheduleType
from app.models.audit_log import AuditAction
from app.schemas.schedule import (
    ScheduleItemCreate, ScheduleItemUpdate,
    ScheduleItemResponse, ScheduleListResponse,
    _coerce_extra,
)

router = APIRouter()


def _serialize(item: ScheduleItem) -> ScheduleItemResponse:
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
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


# ─── Public (any authenticated user) ──────────────────────────────
@router.get("", response_model=ScheduleListResponse)
@router.get("/", response_model=ScheduleListResponse)
def list_schedule(
    type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(ScheduleItem)
    if type:
        try:
            q = q.filter(ScheduleItem.type == ScheduleType(type))
        except ValueError:
            pass
    items = q.order_by(asc(ScheduleItem.start_time)).all()
    return ScheduleListResponse(
        items=[_serialize(i) for i in items],
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
    return _serialize(item)


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
    return _serialize(item)


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

    log_action(
        db, admin, AuditAction.schedule_update,
        f"Updated schedule item #{item.id} '{item.title}'",
        request=request, target_type="schedule", target_id=item.id,
        new_value=item.title,
    )
    db.refresh(item)
    return _serialize(item)


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
    log_action(
        db, admin, AuditAction.schedule_delete,
        f"Deleted schedule item #{item.id} '{title}'",
        request=request, target_type="schedule", target_id=item.id,
        old_value=title,
    )

    db.delete(item)
    db.commit()
    return {"message": f"Schedule item '{title}' deleted"}
