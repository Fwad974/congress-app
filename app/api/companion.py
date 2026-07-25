"""
AI Congress Companion API.

Thin HTTP layer over ``app.core.companion``:

- ``POST /ask``            natural-language Q&A over the program
- ``GET  /briefing``       the day at a glance + energy-aware advice
- ``GET  /nudges``         proactive, timely prompts
- ``GET  /serendipity``    a good thing outside your usual topics
- ``GET  /prep/{id}``      speaker prep for a session you present or chair
- ``GET  /summary/{id}``   smart summary of any session
- ``GET  /guide``          the Dubai guide, from organizer-managed content
- ``GET  /providers``      which LLM providers are configured (organizers)
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.core import companion, llm
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.schedule import ScheduleItem
from app.models.user import User, UserRole

router = APIRouter()

ORGANIZER_ROLES = {UserRole.admin, UserRole.super_admin}
MAX_QUESTION = 500

# Per-user throttle on the LLM-backed /ask endpoint: an attendee can't burn
# unbounded paid LLM spend or starve the shared threadpool. In-process sliding
# window — fine for a single worker; move to Redis for a multi-worker fleet.
_ASK_WINDOW_SECONDS = 60
_ASK_MAX_PER_WINDOW = 15
_ask_hits: dict = {}


def _throttle_ask(user_id: int, now: float) -> None:
    hits = [t for t in _ask_hits.get(user_id, ()) if now - t < _ASK_WINDOW_SECONDS]
    if len(hits) >= _ASK_MAX_PER_WINDOW:
        raise HTTPException(status_code=429,
                            detail="You're asking very quickly — give it a moment and try again.")
    hits.append(now)
    _ask_hits[user_id] = hits


class AskRequest(BaseModel):
    question: str
    # Lets a caller force the deterministic path (used by tests and by the
    # "answer from data only" toggle in the UI).
    allow_llm: bool = True

    @field_validator("question")
    @classmethod
    def v_question(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Ask me something")
        return v[:MAX_QUESTION]


class AskResponse(BaseModel):
    answer: str
    kind: str
    via: str                 # "rules", or the provider that answered
    model: Optional[str] = None
    intent: Optional[str] = None
    items: list = []
    links: list = []


@router.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, request: Request, db: Session = Depends(get_db),
        user: User = Depends(get_current_user)):
    # time.monotonic() via loop; import lazily to avoid a module-level import churn
    import time as _time
    _throttle_ask(user.id, _time.monotonic())
    result = companion.answer(db, user, req.question, allow_llm=req.allow_llm)
    return AskResponse(
        answer=result.get("answer", ""), kind=result.get("kind", "freeform"),
        via=result.get("via", "rules"), model=result.get("model"),
        intent=result.get("intent"), items=result.get("items", []),
        links=result.get("links", []))


@router.get("/briefing")
def briefing(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    data = companion.briefing(db, user)
    data["llm_enabled"] = companion.llm_available()
    data["llm_provider"] = companion.llm_provider()
    return data


@router.get("/providers")
def providers(user: User = Depends(get_current_user)):
    """Which LLM providers are configured and usable (organizers only).

    Reports configuration state — never keys — so an organizer can see why
    free-form answers are or aren't available.
    """
    if user.role not in ORGANIZER_ROLES:
        raise HTTPException(status_code=403, detail="Organizers only")
    return {
        "active": llm.active_provider(),
        "setting": get_settings().LLM_PROVIDER,
        "providers": [row.as_dict() for row in llm.provider_status()],
    }


@router.get("/nudges")
def nudges(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return {"nudges": companion.nudges(db, user)}


@router.get("/serendipity")
def serendipity(db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    return companion.serendipity(db, user)


@router.get("/prep/{session_id}")
def prep(session_id: int, db: Session = Depends(get_db),
         user: User = Depends(get_current_user)):
    """Speaker prep — for the session's presenter, its chair, or an organizer."""
    item = db.query(ScheduleItem).filter(ScheduleItem.id == session_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Session not found")
    allowed = (user.role in ORGANIZER_ROLES or item.speaker_id == user.id
               or item.chair_id == user.id)
    if not allowed:
        raise HTTPException(status_code=403,
                            detail="Speaker prep is for this session's team")
    return companion.speaker_prep(db, user, session_id)


@router.get("/summary/{session_id}")
def summary(session_id: int, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    data = companion.session_summary(db, user, session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")
    return data


@router.get("/guide")
def guide(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return companion.dubai_guide(db)
