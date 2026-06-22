"""
Notes API — each user manages their own notes.

Notes can stand alone or link to a schedule item. Users only ever see and
mutate their own notes here; admin moderation lives in app/api/admin.py.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.note import Note
from app.models.schedule import ScheduleItem
from app.schemas.note import (
    NoteCreate, NoteUpdate, NoteResponse, NoteListResponse,
)

router = APIRouter()


def _titles_for(db: Session, item_ids: Set[int]) -> Dict[int, str]:
    """Map schedule_item_id → title for the given ids (one query)."""
    ids = {i for i in item_ids if i is not None}
    if not ids:
        return {}
    rows = db.query(ScheduleItem.id, ScheduleItem.title).filter(
        ScheduleItem.id.in_(ids)
    ).all()
    return {r[0]: r[1] for r in rows}


def _serialize(note: Note, titles: Dict[int, str]) -> NoteResponse:
    return NoteResponse(
        id=note.id,
        content=note.content,
        schedule_item_id=note.schedule_item_id,
        session_title=titles.get(note.schedule_item_id),
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


@router.get("", response_model=NoteListResponse)
@router.get("/", response_model=NoteListResponse)
def list_notes(
    schedule_item_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(Note).filter(Note.user_id == user.id)
    if schedule_item_id is not None:
        q = q.filter(Note.schedule_item_id == schedule_item_id)
    notes = q.order_by(desc(Note.updated_at)).all()
    titles = _titles_for(db, {n.schedule_item_id for n in notes})
    return NoteListResponse(
        notes=[_serialize(n, titles) for n in notes],
        total=len(notes),
    )


@router.post("", response_model=NoteResponse, status_code=201)
@router.post("/", response_model=NoteResponse, status_code=201)
def create_note(
    req: NoteCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if req.schedule_item_id is not None:
        exists = db.query(ScheduleItem.id).filter(
            ScheduleItem.id == req.schedule_item_id
        ).first()
        if not exists:
            raise HTTPException(status_code=400, detail="Schedule item not found")

    note = Note(
        user_id=user.id,
        schedule_item_id=req.schedule_item_id,
        content=req.content,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    titles = _titles_for(db, {note.schedule_item_id})
    return _serialize(note, titles)


@router.put("/{note_id}", response_model=NoteResponse)
def update_note(
    note_id: int,
    req: NoteUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    note = db.query(Note).filter(
        Note.id == note_id, Note.user_id == user.id
    ).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    if req.content is not None:
        note.content = req.content
    # schedule_item_id is only changed when the field is present in the request.
    if "schedule_item_id" in req.model_fields_set:
        if req.schedule_item_id is not None:
            exists = db.query(ScheduleItem.id).filter(
                ScheduleItem.id == req.schedule_item_id
            ).first()
            if not exists:
                raise HTTPException(status_code=400, detail="Schedule item not found")
        note.schedule_item_id = req.schedule_item_id

    note.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(note)
    titles = _titles_for(db, {note.schedule_item_id})
    return _serialize(note, titles)


@router.delete("/{note_id}")
def delete_note(
    note_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    deleted = db.query(Note).filter(
        Note.id == note_id, Note.user_id == user.id
    ).delete(synchronize_session=False)
    db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"message": "Note deleted"}
