"""
Notification fan-out — turn an admin change to a schedule item into per-user
feed entries for everyone who bookmarked that item.

We only *add* rows (and flush so callers can read counts); committing is left to
the caller so the notifications land in the same transaction as the schedule
mutation and its audit-log entry.
"""
from datetime import datetime, time
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

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


def create_broadcast(
    db: Session,
    actor: User,
    title: str,
    body: str,
    target_roles: List[str],
    emergency: bool = False,
) -> Tuple[Broadcast, List[int]]:
    """Fan an announcement out to every active user in the target roles.

    `target_roles` empty = everyone. Returns the Broadcast row and the list of
    recipient user ids (for web-push delivery). Caller commits.
    """
    q = db.query(User.id).filter(User.is_active == True)  # noqa: E712
    if target_roles:
        valid = {r.value for r in UserRole}
        roles = [UserRole(r) for r in target_roles if r in valid]
        q = q.filter(User.role.in_(roles))
    user_ids = [r[0] for r in q.all() if r[0] != actor.id]

    kind = "emergency" if emergency else "announcement"
    for uid in user_ids:
        db.add(UserNotification(
            user_id=uid, schedule_item_id=None, kind=kind, title=title, body=body,
        ))
    bc = Broadcast(
        actor_id=actor.id, actor_email=actor.email, title=title, body=body,
        target_roles=list(target_roles or []), emergency=emergency,
        recipient_count=len(user_ids),
    )
    db.add(bc)
    db.flush()
    return bc, user_ids
