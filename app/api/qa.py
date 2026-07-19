"""
Live Q&A API.

- Any authenticated attendee can submit questions for a session (schedule item)
  and upvote others'.
- Session chairs / moderators / admins can mark questions answered or hide them,
  and delete any question. Authors can delete their own.
- A Server-Sent Events stream pushes create/update/delete events so every open
  client (and the big screen) stays live. Fan-out across workers goes through
  the realtime broadcaster (Redis when configured).
"""
import asyncio
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.admin_security import ROLE_HIERARCHY
from app.core.audit_service import log_action
from app.core.realtime import broadcaster, qa_channel
from app.models.user import User, UserRole
from app.models.audit_log import AuditAction
from app.models.schedule import ScheduleItem
from app.models.qa import Question, QuestionVote, QuestionStatus
from app.schemas.qa import (
    QuestionCreate, QuestionStatusUpdate,
    QuestionResponse, QuestionListResponse,
)

router = APIRouter()

# Roles that can moderate Q&A (session chair and above).
_MODERATE_MIN = ROLE_HIERARCHY[UserRole.session_chair]
# Lightweight anti-spam: one question per user per N seconds.
_SUBMIT_COOLDOWN = 2.0
_last_submit: dict[int, float] = {}


def _can_moderate(user: User) -> bool:
    return ROLE_HIERARCHY.get(user.role, 0) >= _MODERATE_MIN


def _can_moderate_session(db: Session, user: User, session_id: int) -> bool:
    """Chairs/moderators/admins, plus the session's own presenter."""
    if _can_moderate(user):
        return True
    speaker_id = db.query(ScheduleItem.speaker_id).filter(
        ScheduleItem.id == session_id
    ).scalar()
    return speaker_id is not None and speaker_id == user.id


def _recount_upvotes(db: Session, question_id: int) -> int:
    return db.query(QuestionVote).filter(
        QuestionVote.question_id == question_id
    ).count()


def _serialize(q: Question, author_name: str, *,
               is_mine: bool = False, has_upvoted: bool = False) -> QuestionResponse:
    return QuestionResponse(
        id=q.id, session_id=q.schedule_item_id, text=q.text,
        status=q.status.value, upvotes=q.upvotes,
        author_id=q.user_id, author_name=author_name,
        is_mine=is_mine, has_upvoted=has_upvoted, created_at=q.created_at,
    )


async def _broadcast(db: Session, q: Question, event_type: str) -> None:
    """Push a Q&A event to all subscribers of the session's channel."""
    if event_type == "deleted":
        payload = {"type": "deleted", "id": q.id, "session_id": q.schedule_item_id}
    else:
        author = db.query(User.full_name).filter(User.id == q.user_id).scalar() or "Someone"
        payload = {"type": event_type,
                   "question": _serialize(q, author).model_dump(mode="json")}
    await broadcaster.publish(qa_channel(q.schedule_item_id), payload)


def _require_session(db: Session, session_id: int) -> ScheduleItem:
    item = db.query(ScheduleItem).filter(ScheduleItem.id == session_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Session not found")
    return item


# ─── List ─────────────────────────────────────────────────────────
@router.get("/sessions/{session_id}/questions", response_model=QuestionListResponse)
def list_questions(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_session(db, session_id)
    can_mod = _can_moderate_session(db, user, session_id)

    q = db.query(Question, User.full_name).join(
        User, Question.user_id == User.id
    ).filter(Question.schedule_item_id == session_id)
    if not can_mod:
        # Attendees never see hidden questions.
        q = q.filter(Question.status != QuestionStatus.hidden)

    # Open first, then answered; within each, most upvoted, then newest.
    rows = q.order_by(
        asc(Question.status == QuestionStatus.answered),
        desc(Question.upvotes), desc(Question.created_at),
    ).all()

    voted = set()
    if rows:
        ids = [r[0].id for r in rows]
        voted = {
            v[0] for v in db.query(QuestionVote.question_id).filter(
                QuestionVote.user_id == user.id,
                QuestionVote.question_id.in_(ids),
            ).all()
        }

    questions = [_serialize(
        qq, name, is_mine=(qq.user_id == user.id), has_upvoted=(qq.id in voted),
    ) for qq, name in rows]
    return QuestionListResponse(
        questions=questions, total=len(questions), can_moderate=can_mod,
    )


# ─── Submit ───────────────────────────────────────────────────────
@router.post("/sessions/{session_id}/questions",
             response_model=QuestionResponse, status_code=201)
async def create_question(
    session_id: int,
    req: QuestionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_session(db, session_id)

    now = time.monotonic()
    last = _last_submit.get(user.id, 0.0)
    if now - last < _SUBMIT_COOLDOWN:
        raise HTTPException(status_code=429, detail="Slow down a moment before asking again")
    _last_submit[user.id] = now

    q = Question(schedule_item_id=session_id, user_id=user.id, text=req.text)
    db.add(q)
    db.commit()
    db.refresh(q)

    await _broadcast(db, q, "created")
    return _serialize(q, user.full_name, is_mine=True, has_upvoted=False)


# ─── Upvote ───────────────────────────────────────────────────────
@router.post("/questions/{question_id}/upvote", response_model=QuestionResponse)
async def upvote(
    question_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q or q.status == QuestionStatus.hidden:
        raise HTTPException(status_code=404, detail="Question not found")

    existing = db.query(QuestionVote).filter(
        QuestionVote.question_id == question_id,
        QuestionVote.user_id == user.id,
    ).first()
    if not existing:
        db.add(QuestionVote(question_id=question_id, user_id=user.id))
        db.flush()
        q.upvotes = _recount_upvotes(db, question_id)
        q.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(q)
        await _broadcast(db, q, "updated")

    author = db.query(User.full_name).filter(User.id == q.user_id).scalar() or "Someone"
    return _serialize(q, author, is_mine=(q.user_id == user.id), has_upvoted=True)


@router.delete("/questions/{question_id}/upvote", response_model=QuestionResponse)
async def remove_upvote(
    question_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    deleted = db.query(QuestionVote).filter(
        QuestionVote.question_id == question_id,
        QuestionVote.user_id == user.id,
    ).delete(synchronize_session=False)
    if deleted:
        q.upvotes = _recount_upvotes(db, question_id)
        q.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(q)
        await _broadcast(db, q, "updated")

    author = db.query(User.full_name).filter(User.id == q.user_id).scalar() or "Someone"
    return _serialize(q, author, is_mine=(q.user_id == user.id), has_upvoted=False)


# ─── Moderate (chairs / moderators / admins) ─────────────────────
@router.put("/questions/{question_id}/status", response_model=QuestionResponse)
async def set_status(
    question_id: int,
    req: QuestionStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")
    if not _can_moderate_session(db, user, q.schedule_item_id):
        raise HTTPException(status_code=403, detail="Not allowed to moderate Q&A")

    old = q.status.value
    q.status = QuestionStatus(req.status)
    q.updated_at = datetime.now(timezone.utc)
    db.flush()
    log_action(
        db, user, AuditAction.question_moderate,
        f"Set question #{q.id} status: {old} → {q.status.value}",
        request=request, target_type="question", target_id=q.id,
        old_value=old, new_value=q.status.value,
    )
    db.refresh(q)
    await _broadcast(db, q, "updated")

    author = db.query(User.full_name).filter(User.id == q.user_id).scalar() or "Someone"
    return _serialize(q, author, is_mine=(q.user_id == user.id))


# ─── Delete (author or moderator) ────────────────────────────────
@router.delete("/questions/{question_id}")
async def delete_question(
    question_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    is_owner = q.user_id == user.id
    if not is_owner and not _can_moderate_session(db, user, q.schedule_item_id):
        raise HTTPException(status_code=403, detail="Not allowed to delete this question")

    session_id = q.schedule_item_id
    if not is_owner:
        log_action(
            db, user, AuditAction.question_moderate,
            f"Deleted question #{q.id}",
            request=request, target_type="question", target_id=q.id,
            old_value=(q.text or "")[:80],
        )
    db.delete(q)
    db.commit()

    await broadcaster.publish(qa_channel(session_id),
                              {"type": "deleted", "id": question_id, "session_id": session_id})
    return {"message": "Question deleted"}


# ─── Live stream (SSE) ────────────────────────────────────────────
@router.get("/sessions/{session_id}/qa/stream")
async def qa_stream(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_session(db, session_id)
    channel = qa_channel(session_id)

    async def event_gen():
        async with broadcaster.subscribe(channel) as q:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    raw = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {raw}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # comment line; keeps the connection warm

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # don't let nginx buffer the stream
        },
    )
