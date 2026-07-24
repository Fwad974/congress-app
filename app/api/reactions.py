"""
Live emoji reactions.

Deliberately ephemeral: reactions are high-volume and low-value to store, so
nothing hits the database. Attendees tap emojis; the client batches taps and
POSTs counts; the server validates + relays a "burst" over SSE. The presenter
(and any client) aggregates a rolling window locally — the speaker sees the
live pulse without a single DB write.
"""
import asyncio
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.realtime import broadcaster, react_channel
from app.models.user import User
from app.models.schedule import ScheduleItem

router = APIRouter()

# The tappable set. 🔥 wow / 💡 insight / 🤔 hmm / 👏 applause / ❤️ love.
ALLOWED_EMOJIS = ["🔥", "💡", "🤔", "👏", "❤️"]
_ALLOWED = set(ALLOWED_EMOJIS)
MAX_PER_EMOJI = 20   # per request
MAX_TOTAL = 60       # per request across all emojis


def _require_session(db: Session, session_id: int) -> None:
    if not db.query(ScheduleItem.id).filter(ScheduleItem.id == session_id).first():
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/reactions/emojis")
def emojis(user: User = Depends(get_current_user)):
    return {"emojis": ALLOWED_EMOJIS}


@router.post("/sessions/{session_id}/reactions")
async def react(
    session_id: int,
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_session(db, session_id)
    from app.core.moderation_service import ensure_can_post
    ensure_can_post(user)

    raw = (body or {}).get("counts") or {}
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="counts must be an object")

    counts: Dict[str, int] = {}
    total = 0
    for emoji, n in raw.items():
        if emoji not in _ALLOWED:
            continue
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        n = min(n, MAX_PER_EMOJI)
        if total + n > MAX_TOTAL:
            n = MAX_TOTAL - total
        if n <= 0:
            break
        counts[emoji] = n
        total += n

    if not counts:
        raise HTTPException(status_code=400, detail="No valid reactions")

    await broadcaster.publish(react_channel(session_id),
                              {"type": "burst", "counts": counts})
    return {"ok": True, "counts": counts}


@router.get("/sessions/{session_id}/reactions/stream")
async def reactions_stream(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_session(db, session_id)
    channel = react_channel(session_id)

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
