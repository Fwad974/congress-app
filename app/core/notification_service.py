"""
Notification fan-out — turn an admin change to a schedule item into per-user
feed entries for everyone who bookmarked that item.

We only *add* rows (and flush so callers can read counts); committing is left to
the caller so the notifications land in the same transaction as the schedule
mutation and its audit-log entry.
"""
from datetime import datetime, time, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.user import User, UserRole
from app.models.schedule import ScheduleBookmark, ScheduleItem
from app.models.notification import UserNotification, Broadcast


def notify_bookmarkers(
    db: Session,
    item: ScheduleItem,
    kind: str,
    body: str,
    exclude_user_id: Optional[int] = None,
) -> List[int]:
    """Create a UserNotification for every user who bookmarked `item`.

    `exclude_user_id` skips one user — typically the admin who made the change,
    so they don't get notified about their own edit. Returns the list of
    recipient user IDs (so the caller can also fan out a web push to them).
    """
    rows = db.query(ScheduleBookmark.user_id).filter(
        ScheduleBookmark.schedule_item_id == item.id
    ).all()
    user_ids = {r[0] for r in rows}
    user_ids.discard(exclude_user_id)

    for uid in user_ids:
        db.add(UserNotification(
            user_id=uid,
            schedule_item_id=item.id,
            kind=kind,
            title=item.title,
            body=body,
        ))
    db.flush()
    return sorted(user_ids)


def _parse_hhmm(s: str) -> Optional[time]:
    try:
        h, m = (s or "").split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return None


def in_quiet_hours(prefs: dict, now_utc: datetime) -> bool:
    """True if `now_utc` falls inside the user's quiet-hours window.

    Needs the user's IANA `tz` in prefs (the client stores it). Without a tz we
    can't localize, so we treat it as *not* quiet (fail open — deliver).
    """
    qh = (prefs or {}).get("quiet_hours") or {}
    if not qh.get("enabled"):
        return False
    tz = (prefs or {}).get("tz") or ""
    if not tz:
        return False
    try:
        local = now_utc.astimezone(ZoneInfo(tz))
    except Exception:  # noqa: BLE001 - unknown tz → deliver
        return False
    start = _parse_hhmm(qh.get("start", "22:00"))
    end = _parse_hhmm(qh.get("end", "07:00"))
    if not start or not end:
        return False
    t = local.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end          # overnight window (e.g. 22:00 → 07:00)


def _parse_dt(s: Optional[str], end: bool = False) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if len(s) <= 10:  # date only → span the whole day
        d = d.replace(hour=23, minute=59, second=59) if end else d.replace(hour=0, minute=0, second=0)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def resolve_audience(db: Session, spec, actor_id: Optional[int] = None) -> List[int]:
    """Turn an AudienceSpec into the list of active recipient user ids.

    Filters (roles, paper status, reviewer/speaker, attendance, registration
    window) are ANDed. Explicit `user_ids` are unioned on top. No filters and no
    picks → everyone. The actor is excluded (you don't notify yourself).
    """
    from app.models.paper import Paper, Review, PaperStatus
    from app.models.attendance import SessionAttendance

    base = db.query(User.id).filter(User.is_active == True)  # noqa: E712
    applied = False

    roles = getattr(spec, "roles", None) or []
    if roles:
        valid = {r.value for r in UserRole}
        members = [UserRole(r) for r in roles if r in valid]
        if members:
            base = base.filter(User.role.in_(members)); applied = True

    ra = _parse_dt(getattr(spec, "registered_after", None))
    rb = _parse_dt(getattr(spec, "registered_before", None), end=True)
    if ra:
        base = base.filter(User.created_at >= ra); applied = True
    if rb:
        base = base.filter(User.created_at <= rb); applied = True

    pstatus = getattr(spec, "paper_status", None) or []
    if pstatus:
        valid = {s.value for s in PaperStatus}
        members = [PaperStatus(s) for s in pstatus if s in valid]
        if members:
            sub = db.query(Paper.author_id).filter(Paper.status.in_(members)).distinct()
            base = base.filter(User.id.in_(sub)); applied = True

    if getattr(spec, "is_reviewer", False):
        base = base.filter(User.id.in_(db.query(Review.reviewer_id).distinct()))
        applied = True

    if getattr(spec, "is_speaker", False):
        base = base.filter(User.id.in_(
            db.query(ScheduleItem.speaker_id).filter(ScheduleItem.speaker_id.isnot(None)).distinct()))
        applied = True

    amin = getattr(spec, "attended_min", None)
    if amin and amin > 0:
        sub = (db.query(SessionAttendance.user_id)
               .group_by(SessionAttendance.user_id)
               .having(func.count(SessionAttendance.id) >= amin))
        base = base.filter(User.id.in_(sub)); applied = True

    picks = getattr(spec, "user_ids", None) or []
    ids = set()
    if applied or not picks:
        ids |= {r[0] for r in base.all()}
    if picks:
        chosen = db.query(User.id).filter(
            User.id.in_(picks), User.is_active == True)  # noqa: E712
        ids |= {r[0] for r in chosen.all()}
    if actor_id is not None:
        ids.discard(actor_id)
    return sorted(ids)


def audience_summary(spec) -> str:
    """Human-readable description of a targeting spec, for history/audit."""
    if spec is None:
        return "everyone"
    parts = []
    roles = getattr(spec, "roles", None) or []
    if roles:
        parts.append("roles: " + ", ".join(r.replace("_", " ") for r in roles))
    pstatus = getattr(spec, "paper_status", None) or []
    if pstatus:
        parts.append("paper " + "/".join(pstatus))
    if getattr(spec, "is_reviewer", False):
        parts.append("reviewers")
    if getattr(spec, "is_speaker", False):
        parts.append("speakers")
    amin = getattr(spec, "attended_min", None)
    if amin:
        parts.append(f"attended ≥{amin}")
    if getattr(spec, "registered_after", None):
        parts.append(f"registered ≥ {spec.registered_after}")
    if getattr(spec, "registered_before", None):
        parts.append(f"registered ≤ {spec.registered_before}")
    picks = getattr(spec, "user_ids", None) or []
    if picks:
        parts.append(f"{len(picks)} named")
    return "; ".join(parts) if parts else "everyone"


def create_broadcast(
    db: Session,
    actor: User,
    title: str,
    body: str,
    recipient_ids: List[int],
    emergency: bool = False,
    target_roles: Optional[List[str]] = None,
    summary: str = "everyone",
) -> Tuple[Broadcast, List[int]]:
    """Write per-user notifications for the resolved recipients + a history row.

    Returns the Broadcast row and the recipient ids (for web-push delivery).
    Caller commits.
    """
    recipient_ids = list(recipient_ids)
    kind = "emergency" if emergency else "announcement"
    for uid in recipient_ids:
        db.add(UserNotification(
            user_id=uid, schedule_item_id=None, kind=kind, title=title, body=body,
        ))
    bc = Broadcast(
        actor_id=actor.id, actor_email=actor.email, title=title, body=body,
        target_roles=list(target_roles or []), emergency=emergency,
        recipient_count=len(recipient_ids), audience_summary=summary,
    )
    db.add(bc)
    db.flush()
    return bc, recipient_ids
