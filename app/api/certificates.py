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

Unlocked certificates can be **downloaded as a PDF** carrying a unique
serial and a QR that resolves to the public verification page
(``/verify/{serial}``), and attendees can **export a CSV attendance
report** for their institution.
"""
import csv
import io
import math
import secrets
import uuid
from datetime import datetime, timezone
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
from app.models.schedule import ScheduleItem, ScheduleType
from app.models.attendance import SessionAttendance
from app.models.certificate import IssuedCertificate

router = APIRouter()

CERT_KINDS = ("attendance", "cme", "speaker")
_SERIAL_CODES = {"attendance": "ATT", "cme": "CME", "speaker": "SPK"}


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


def attendance_goal(db: Session) -> int:
    """Sessions needed for the attendance certificate.

    Fixed count by default (CERT_MIN_SESSIONS); when CERT_ATTENDANCE_PCT > 0
    the goal is that percentage of the program (non-break schedule items).
    """
    s = get_settings()
    if s.CERT_ATTENDANCE_PCT > 0:
        total = db.query(func.count(ScheduleItem.id)).filter(
            ScheduleItem.type != ScheduleType.break_).scalar() or 0
        if total:
            return max(1, math.ceil(total * s.CERT_ATTENDANCE_PCT / 100))
    return s.CERT_MIN_SESSIONS


def cert_status(db: Session, user: User) -> CertStatus:
    s = get_settings()
    attended = _attended_count(db, user.id)
    credits = attended * s.CME_CREDITS_PER_SESSION
    spoke = _is_speaker_of_any(db, user.id)
    att_goal = attendance_goal(db)
    att_desc = (
        f"Check in to {att_goal}+ sessions ({s.CERT_ATTENDANCE_PCT}% of the program) "
        "by scanning the QR at the door."
        if s.CERT_ATTENDANCE_PCT > 0 else
        f"Check in to {att_goal}+ sessions by scanning the QR at the door.")
    certs = [
        CertInfo(kind="attendance", title="Certificate of Attendance",
                 description=att_desc,
                 unlocked=attended >= att_goal,
                 progress=min(attended, att_goal),
                 goal=att_goal, unit="sessions"),
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


# ─── Certificate issuance, PDF download & verification ──────────
def _public_base(request: Request) -> str:
    return (get_settings().OAUTH_REDIRECT_BASE or str(request.base_url)).rstrip("/")


def ensure_issued(db: Session, user: User, kind: str,
                  status: CertStatus) -> IssuedCertificate:
    """Get or create the persistent record behind an unlocked certificate.

    First access mints the unique serial and freezes ``issued_at``. The
    attested counts are refreshed upward if the holder attends more sessions
    and re-downloads, so /verify always matches the latest copy in the wild.
    """
    rec = db.query(IssuedCertificate).filter(
        IssuedCertificate.user_id == user.id,
        IssuedCertificate.kind == kind).first()
    if not rec:
        prefix = f"DSCC{get_settings().CONGRESS_YEAR}-{_SERIAL_CODES[kind]}"
        serial = None
        for _ in range(10):
            candidate = f"{prefix}-{secrets.token_hex(4).upper()}"
            if not db.query(IssuedCertificate.id).filter(
                    IssuedCertificate.serial == candidate).first():
                serial = candidate
                break
        rec = IssuedCertificate(
            user_id=user.id, kind=kind,
            serial=serial or f"{prefix}-{secrets.token_hex(8).upper()}",
            sessions_count=status.attended_sessions, credits=status.credits)
        db.add(rec)
        db.commit()
        db.refresh(rec)
    elif (status.attended_sessions > rec.sessions_count
          or status.credits > rec.credits):
        rec.sessions_count = max(rec.sessions_count, status.attended_sessions)
        rec.credits = max(rec.credits, status.credits)
        db.commit()
    return rec


def _cert_body_text(cert: CertInfo, credits: int, s) -> str:
    full = f"{s.CONGRESS_NAME} {s.CONGRESS_YEAR}"
    where = f"held {s.CONGRESS_DATES} at {s.CONGRESS_VENUE}."
    if cert.kind == "attendance":
        return f"in recognition of their attendance at {full}, {where}"
    if cert.kind == "cme":
        unit = "credit" if credits == 1 else "credits"
        return (f"for earning {credits} CME/CPD {unit} through participation "
                f"in accredited sessions at {full}, {where}")
    return (f"in recognition of their contribution as a speaker at {full}, "
            f"{where}")


@router.get("/certificates/verify/{serial}")
def verify_certificate(serial: str, db: Session = Depends(get_db)):
    """Public verification — no login, so institutions can check a serial."""
    s = get_settings()
    rec = (db.query(IssuedCertificate, User)
           .join(User, User.id == IssuedCertificate.user_id)
           .filter(IssuedCertificate.serial == serial.strip().upper()).first())
    if not rec:
        raise HTTPException(status_code=404, detail="Certificate not found")
    cert, holder = rec
    titles = {"attendance": "Certificate of Attendance",
              "cme": "CME/CPD Credit Certificate",
              "speaker": "Speaker Certificate"}
    return {
        "valid": not cert.revoked,
        "status": "revoked" if cert.revoked else "valid",
        "serial": cert.serial,
        "kind": cert.kind,
        "title": titles.get(cert.kind, cert.kind),
        "holder": holder.full_name,
        "institution": holder.institution,
        "issued_on": cert.issued_at.strftime("%d %B %Y"),
        "sessions": cert.sessions_count,
        "credits": cert.credits,
        "congress": f"{s.CONGRESS_NAME} {s.CONGRESS_YEAR}",
    }


@router.get("/certificates/{kind}/download")
def download_certificate(kind: str, request: Request,
                         db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    """Downloadable PDF with an embedded verification QR + serial."""
    if kind not in CERT_KINDS:
        raise HTTPException(status_code=404, detail="Unknown certificate")
    status = cert_status(db, user)
    cert = next((c for c in status.certificates if c.kind == kind), None)
    if not cert or not cert.unlocked:
        raise HTTPException(status_code=403, detail="Certificate not unlocked yet")
    rec = ensure_issued(db, user, kind, status)

    s = get_settings()
    from app.core.pdf import certificate_pdf
    pdf = certificate_pdf(
        holder=user.full_name,
        cert_title=cert.title,
        body_text=_cert_body_text(cert, rec.credits, s),
        issued_on=rec.issued_at.strftime("%d %B %Y"),
        serial=rec.serial,
        verify_url=f"{_public_base(request)}/verify/{rec.serial}",
        congress_full=f"{s.CONGRESS_NAME} {s.CONGRESS_YEAR}",
        congress_short=f"DSCC {s.CONGRESS_YEAR}",
    )
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="certificate-{kind}-{rec.serial}.pdf"'})


# ─── Attendance report (CSV, for institutions) ──────────────────
@router.get("/attendance/report/export")
def export_attendance_report(request: Request, db: Session = Depends(get_db),
                             user: User = Depends(get_current_user)):
    """CSV report of every attended session, credits, and issued serials."""
    s = get_settings()
    rows = (db.query(SessionAttendance, ScheduleItem)
            .join(ScheduleItem, ScheduleItem.id == SessionAttendance.schedule_item_id)
            .filter(SessionAttendance.user_id == user.id)
            .order_by(ScheduleItem.start_time.asc()).all())

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"Attendance Report — {s.CONGRESS_NAME} {s.CONGRESS_YEAR}"])
    w.writerow(["Attendee", user.full_name])
    w.writerow(["Email", user.email])
    w.writerow(["Institution", user.institution or ""])
    w.writerow(["Generated (UTC)",
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")])
    w.writerow([])
    w.writerow(["Session", "Type", "Location", "Session start (UTC)",
                "Checked in (UTC)", "Credits"])
    for att, item in rows:
        w.writerow([
            item.title,
            item.type.value if item.type else "",
            item.location or "",
            item.start_time.strftime("%Y-%m-%d %H:%M") if item.start_time else "",
            att.created_at.strftime("%Y-%m-%d %H:%M") if att.created_at else "",
            s.CME_CREDITS_PER_SESSION,
        ])
    w.writerow([])
    w.writerow(["Total sessions attended", len(rows)])
    w.writerow(["Total CME/CPD credits", len(rows) * s.CME_CREDITS_PER_SESSION])

    issued = db.query(IssuedCertificate).filter(
        IssuedCertificate.user_id == user.id,
        IssuedCertificate.revoked.is_(False)).all()
    if issued:
        base = _public_base(request)
        w.writerow([])
        w.writerow(["Certificate", "Serial", "Issued on", "Verify at"])
        for cert in issued:
            w.writerow([cert.kind, cert.serial,
                        cert.issued_at.strftime("%Y-%m-%d"),
                        f"{base}/verify/{cert.serial}"])

    return Response(
        content=buf.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 'attachment; filename="attendance-report.csv"'})


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
