"""
Certificates & CME credits.

Attendance is recorded by scanning a QR at the session door (or typing its
code). Certificates unlock from attendance / speaking:

- **attendance** — attended ≥ ``CERT_MIN_SESSIONS`` sessions.
- **cme** — earned ≥ ``CME_MIN_CREDITS`` credits
  (``CME_CREDITS_PER_SESSION`` per attended session).
- **speaker** — presenter (``speaker_id``) on at least one schedule item.

Admins print each session's QR from the schedule page; the QR encodes
``/certificates?checkin=CODE`` so scanning opens the app and records
attendance.
"""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.core.admin_security import require_admin
from app.models.user import User
from app.models.schedule import ScheduleItem
from app.models.attendance import SessionAttendance

router = APIRouter()

CERT_KINDS = ("attendance", "cme", "speaker")


# ─── Schemas ─────────────────────────────────────────────────────
class CheckinRequest(BaseModel):
    code: str

    @field_validator("code")
    @classmethod
    def v_code(cls, v):
        v = (v or "").strip().upper()
        if not v:
            raise ValueError("Code is required")
        return v[:12]


class CheckinResponse(BaseModel):
    session_title: str
    attended: int
    already: bool = False


class CertInfo(BaseModel):
    kind: str
    title: str
    description: str
    unlocked: bool
    progress: int
    goal: int
    unit: str


class CertStatus(BaseModel):
    attended_sessions: int
    credits: int
    certificates: List[CertInfo]


class AttendedItem(BaseModel):
    schedule_item_id: int
    title: str
    checked_in_at: str


# ─── Helpers ─────────────────────────────────────────────────────
def _attended_count(db: Session, user_id: int) -> int:
    return db.query(func.count(SessionAttendance.id)).filter(
        SessionAttendance.user_id == user_id).scalar() or 0


def _is_speaker_of_any(db: Session, user_id: int) -> bool:
    return db.query(ScheduleItem.id).filter(
        ScheduleItem.speaker_id == user_id).first() is not None


def cert_status(db: Session, user: User) -> CertStatus:
    s = get_settings()
    attended = _attended_count(db, user.id)
    credits = attended * s.CME_CREDITS_PER_SESSION
    spoke = _is_speaker_of_any(db, user.id)
    certs = [
        CertInfo(kind="attendance", title="Certificate of Attendance",
                 description=f"Check in to {s.CERT_MIN_SESSIONS}+ sessions by scanning the QR at the door.",
                 unlocked=attended >= s.CERT_MIN_SESSIONS,
                 progress=min(attended, s.CERT_MIN_SESSIONS),
                 goal=s.CERT_MIN_SESSIONS, unit="sessions"),
        CertInfo(kind="cme", title="CME/CPD Credit Certificate",
                 description=f"Earn {s.CME_MIN_CREDITS}+ credits ({s.CME_CREDITS_PER_SESSION} per attended session).",
                 unlocked=credits >= s.CME_MIN_CREDITS,
                 progress=min(credits, s.CME_MIN_CREDITS),
                 goal=s.CME_MIN_CREDITS, unit="credits"),
        CertInfo(kind="speaker", title="Speaker Certificate",
                 description="Issued to presenters of a session in the program.",
                 unlocked=spoke, progress=1 if spoke else 0, goal=1, unit="sessions presented"),
    ]
    return CertStatus(attended_sessions=attended, credits=credits, certificates=certs)


def _cert_by_kind(db: Session, user: User, kind: str) -> Optional[CertInfo]:
    return next((c for c in cert_status(db, user).certificates if c.kind == kind), None)


# ─── Attendance check-in ─────────────────────────────────────────
@router.post("/attendance/checkin", response_model=CheckinResponse)
def checkin(req: CheckinRequest, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    item = db.query(ScheduleItem).filter(ScheduleItem.attend_code == req.code).first()
    if not item:
        raise HTTPException(status_code=404, detail="No session matches that code")
    already = db.query(SessionAttendance.id).filter(
        SessionAttendance.schedule_item_id == item.id,
        SessionAttendance.user_id == user.id).first() is not None
    if not already:
        db.add(SessionAttendance(schedule_item_id=item.id, user_id=user.id))
        db.commit()
    return CheckinResponse(session_title=item.title,
                           attended=_attended_count(db, user.id), already=already)


@router.get("/attendance/mine", response_model=List[AttendedItem])
def my_attendance(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (db.query(SessionAttendance, ScheduleItem.title)
            .join(ScheduleItem, ScheduleItem.id == SessionAttendance.schedule_item_id)
            .filter(SessionAttendance.user_id == user.id)
            .order_by(SessionAttendance.created_at.desc()).all())
    return [AttendedItem(schedule_item_id=a.schedule_item_id, title=t,
                         checked_in_at=a.created_at.isoformat()) for a, t in rows]


# ─── Certificate status ──────────────────────────────────────────
@router.get("/certificates/status", response_model=CertStatus)
def certificates_status(db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    return cert_status(db, user)


# ─── Session QR (admin) ──────────────────────────────────────────
def _attend_url(request: Request, code: str) -> str:
    base = (get_settings().OAUTH_REDIRECT_BASE or str(request.base_url)).rstrip("/")
    return f"{base}/certificates?checkin={code}"


def ensure_attend_code(db: Session, item: ScheduleItem) -> str:
    if not item.attend_code:
        for _ in range(10):
            code = uuid.uuid4().hex[:8].upper()
            if not db.query(ScheduleItem.id).filter(
                    ScheduleItem.attend_code == code).first():
                item.attend_code = code
                break
        else:
            item.attend_code = uuid.uuid4().hex[:12].upper()
        db.commit()
    return item.attend_code


@router.get("/schedule/{item_id}/qr")
def session_qr(item_id: int, request: Request, db: Session = Depends(get_db),
               admin: User = Depends(require_admin())):
    item = db.query(ScheduleItem).filter(ScheduleItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Session not found")
    code = ensure_attend_code(db, item)
    from app.core.qr import qr_svg_document
    return Response(content=qr_svg_document(_attend_url(request, code)),
                    media_type="image/svg+xml")
