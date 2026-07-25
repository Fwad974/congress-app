"""
Live polls API.

- Session chairs (and above) create polls, open/close them, and delete them.
- Attendees vote while a poll is open: pick one/many options, or submit terms
  for a word cloud.
- Results are aggregated from PollResponse rows (never drift) and pushed live
  over SSE so the presenter big-screen and every voter update together.
"""
import asyncio
import re
from collections import Counter
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, asc

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.admin_security import can_moderate_session
from app.core.realtime import broadcaster, poll_channel
from app.models.user import User
from app.models.schedule import ScheduleItem
from app.models.poll import Poll, PollOption, PollResponse, PollType, PollStatus
from app.schemas.poll import (
    PollCreate, VoteRequest, OptionResult, WordCount, PollResults,
    PollResponse as PollOut, PollListResponse,
    MAX_WORD_LEN, WORDCLOUD_MAX_PER_USER,
)

router = APIRouter()

_WS = re.compile(r"\s+")


def _norm_word(s: str) -> str:
    if not s:
        return ""
    s = _WS.sub(" ", s.strip()).lower()
    return s[:MAX_WORD_LEN]


def _require_session(db: Session, session_id: int) -> ScheduleItem:
    item = db.query(ScheduleItem).filter(ScheduleItem.id == session_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Session not found")
    return item


def _require_session_mod(db: Session, user: User, session_id: int) -> None:
    if not can_moderate_session(db, user, session_id):
        raise HTTPException(status_code=403, detail="Not allowed to run polls for this session")


def _aggregate(db: Session, poll: Poll) -> PollResults:
    if poll.type == PollType.wordcloud:
        rows = db.query(PollResponse.text).filter(
            PollResponse.poll_id == poll.id, PollResponse.text.isnot(None)
        ).all()
        counter: Counter = Counter()
        for (t,) in rows:
            w = _norm_word(t)
            if w:
                counter[w] += 1
        words = [WordCount(text=w, count=n) for w, n in counter.most_common(50)]
        return PollResults(poll_id=poll.id, type=poll.type.value,
                           total=sum(counter.values()), words=words)

    options = db.query(PollOption).filter(
        PollOption.poll_id == poll.id
    ).order_by(asc(PollOption.position), asc(PollOption.id)).all()
    counts = dict(
        db.query(PollResponse.option_id, func.count(PollResponse.id))
        .filter(PollResponse.poll_id == poll.id, PollResponse.option_id.isnot(None))
        .group_by(PollResponse.option_id).all()
    )
    opts = [OptionResult(id=o.id, text=o.text, count=counts.get(o.id, 0))
            for o in options]
    return PollResults(poll_id=poll.id, type=poll.type.value,
                       total=sum(counts.values()), options=opts)


def _my_responses(db: Session, poll: Poll, user_id: int):
    rows = db.query(PollResponse.option_id, PollResponse.text).filter(
        PollResponse.poll_id == poll.id, PollResponse.user_id == user_id
    ).all()
    option_ids = [r[0] for r in rows if r[0] is not None]
    words = [r[1] for r in rows if r[1] is not None]
    return option_ids, words


def _serialize(db: Session, poll: Poll, user: User) -> PollOut:
    agg = _aggregate(db, poll)
    my_options, my_words = _my_responses(db, poll, user.id)
    return PollOut(
        id=poll.id, session_id=poll.schedule_item_id, question=poll.question,
        type=poll.type.value, status=poll.status.value,
        options=agg.options, my_option_ids=my_options, my_words=my_words,
        can_moderate=can_moderate_session(db, user, poll.schedule_item_id),
    )


async def _publish(session_id: int, payload: dict) -> None:
    await broadcaster.publish(poll_channel(session_id), payload)


# ─── Create / list ───────────────────────────────────────────────
@router.post("/sessions/{session_id}/polls", response_model=PollOut, status_code=201)
def create_poll(
    session_id: int,
    req: PollCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_session(db, session_id)
    _require_session_mod(db, user, session_id)

    ptype = PollType(req.type)
    if ptype in (PollType.single, PollType.multiple) and len(req.options) < 2:
        raise HTTPException(status_code=400, detail="Choice polls need at least 2 options")

    poll = Poll(schedule_item_id=session_id, created_by=user.id,
                question=req.question, type=ptype, status=PollStatus.draft)
    db.add(poll)
    db.flush()
    if ptype in (PollType.single, PollType.multiple):
        for i, text in enumerate(req.options):
            db.add(PollOption(poll_id=poll.id, text=text, position=i))
    db.commit()
    db.refresh(poll)
    return _serialize(db, poll, user)


@router.get("/sessions/{session_id}/polls", response_model=PollListResponse)
def list_polls(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_session(db, session_id)
    can_mod = can_moderate_session(db, user, session_id)
    q = db.query(Poll).filter(Poll.schedule_item_id == session_id)
    if not can_mod:
        # Attendees never see drafts.
        q = q.filter(Poll.status != PollStatus.draft)
    polls = q.order_by(asc(Poll.created_at)).all()
    return PollListResponse(
        polls=[_serialize(db, p, user) for p in polls], can_moderate=can_mod,
    )


@router.get("/polls/{poll_id}", response_model=PollOut)
def get_poll(
    poll_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    poll = db.query(Poll).filter(Poll.id == poll_id).first()
    if not poll or (poll.status == PollStatus.draft and not can_moderate_session(db, user, poll.schedule_item_id)):
        raise HTTPException(status_code=404, detail="Poll not found")
    return _serialize(db, poll, user)


@router.get("/polls/{poll_id}/results", response_model=PollResults)
def get_results(
    poll_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    poll = db.query(Poll).filter(Poll.id == poll_id).first()
    if not poll or (poll.status == PollStatus.draft and not can_moderate_session(db, user, poll.schedule_item_id)):
        raise HTTPException(status_code=404, detail="Poll not found")
    return _aggregate(db, poll)


# ─── Lifecycle (chair) ───────────────────────────────────────────
@router.put("/polls/{poll_id}/status", response_model=PollOut)
async def set_poll_status(
    poll_id: int,
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    poll = db.query(Poll).filter(Poll.id == poll_id).first()
    if not poll:
        raise HTTPException(status_code=404, detail="Poll not found")
    _require_session_mod(db, user, poll.schedule_item_id)
    new_status = (body or {}).get("status")
    if new_status not in {"draft", "open", "closed"}:
        raise HTTPException(status_code=400, detail="Invalid status")

    poll.status = PollStatus(new_status)
    poll.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(poll)

    out = _serialize(db, poll, user)
    agg = _aggregate(db, poll)
    if poll.status == PollStatus.open:
        await _publish(poll.schedule_item_id, {
            "type": "opened", "poll": out.model_dump(mode="json"),
            "results": agg.model_dump(mode="json"),
        })
    elif poll.status == PollStatus.closed:
        await _publish(poll.schedule_item_id, {
            "type": "closed", "poll_id": poll.id,
            "results": agg.model_dump(mode="json"),
        })
    return out


@router.delete("/polls/{poll_id}")
async def delete_poll(
    poll_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    poll = db.query(Poll).filter(Poll.id == poll_id).first()
    if not poll:
        raise HTTPException(status_code=404, detail="Poll not found")
    _require_session_mod(db, user, poll.schedule_item_id)
    session_id = poll.schedule_item_id
    db.delete(poll)
    db.commit()
    await _publish(session_id, {"type": "deleted", "poll_id": poll_id})
    return {"message": "Poll deleted"}


# ─── Vote (attendee) ─────────────────────────────────────────────
@router.post("/polls/{poll_id}/vote", response_model=PollOut)
async def vote(
    poll_id: int,
    req: VoteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    poll = db.query(Poll).filter(Poll.id == poll_id).first()
    if not poll:
        raise HTTPException(status_code=404, detail="Poll not found")
    if poll.status != PollStatus.open:
        raise HTTPException(status_code=400, detail="Poll is not open")

    if poll.type == PollType.wordcloud:
        # A word-cloud submission is free text shown on the big screen, so it's
        # subject to the mute guard (MOD-04); anonymous option votes aren't.
        from app.core.moderation_service import ensure_can_post
        ensure_can_post(user)
        word = _norm_word(req.text or "")
        if not word:
            raise HTTPException(status_code=400, detail="Enter a word or phrase")
        used = db.query(PollResponse).filter(
            PollResponse.poll_id == poll.id, PollResponse.user_id == user.id
        ).count()
        if used >= WORDCLOUD_MAX_PER_USER:
            raise HTTPException(status_code=429,
                                detail=f"You've reached {WORDCLOUD_MAX_PER_USER} submissions")
        db.add(PollResponse(poll_id=poll.id, user_id=user.id, text=word))
    else:
        valid_ids = {
            o[0] for o in db.query(PollOption.id).filter(
                PollOption.poll_id == poll.id).all()
        }
        chosen = [oid for oid in dict.fromkeys(req.option_ids) if oid in valid_ids]
        if poll.type == PollType.single and len(chosen) != 1:
            raise HTTPException(status_code=400, detail="Pick exactly one option")
        # Replace this user's selection.
        db.query(PollResponse).filter(
            PollResponse.poll_id == poll.id, PollResponse.user_id == user.id
        ).delete(synchronize_session=False)
        for oid in chosen:
            db.add(PollResponse(poll_id=poll.id, user_id=user.id, option_id=oid))

    db.commit()
    db.refresh(poll)

    agg = _aggregate(db, poll)
    await _publish(poll.schedule_item_id, {
        "type": "results", "results": agg.model_dump(mode="json"),
    })
    return _serialize(db, poll, user)


# ─── Live stream (SSE) ───────────────────────────────────────────
@router.get("/sessions/{session_id}/polls/stream")
async def polls_stream(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_session(db, session_id)
    channel = poll_channel(session_id)

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
                    yield ": keepalive\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
