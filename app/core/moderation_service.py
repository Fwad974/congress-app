"""
Moderation service: posting guard + the MOD-04 escalation ladder.

- ``ensure_can_post`` — the write-time guard used across content endpoints:
  suspended users can't act at all (they're rejected at auth); muted users can
  browse but not create content until the mute expires.
- ``apply_action`` / ``next_escalation`` — warn → 24h mute → suspend, each
  recorded in ``ModerationAction`` (with a required reason), pushed to the
  target's notification feed, and audit-logged.
- ``auto_flag`` — create/merge a system report when auto-flagging fires, so the
  content shows up in the moderator queue.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.audit_service import log_action
from app.models.user import User, UserRole
from app.models.audit_log import AuditAction
from app.models.moderation import ModerationAction
from app.models.notification import UserNotification
from app.models.report import ContentReport


# ─── Posting guard (mute / suspend enforcement) ──────────────────
def is_muted(user: User) -> bool:
    mu = getattr(user, "muted_until", None)
    if not mu:
        return False
    if mu.tzinfo is None:                     # naive (SQLite) → treat as UTC
        mu = mu.replace(tzinfo=timezone.utc)
    return mu > datetime.now(timezone.utc)


def ensure_can_post(user: User) -> None:
    """Raise 403 if the user is currently barred from creating content."""
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Your account is suspended.")
    if is_muted(user):
        raise HTTPException(
            status_code=403,
            detail="You're temporarily muted by a moderator and can't post right now.")


# ─── Escalation ladder ───────────────────────────────────────────
def _has_prior_warn(db: Session, user_id: int) -> bool:
    return db.query(ModerationAction.id).filter(
        ModerationAction.user_id == user_id,
        ModerationAction.action == "warn").first() is not None


def next_escalation(db: Session, target: User) -> Optional[str]:
    """The next rung for this user: warn → mute → suspend (None if suspended)."""
    if not target.is_active:
        return None
    if is_muted(target):
        return "suspend"
    if _has_prior_warn(db, target.id):
        return "mute"
    return "warn"


_NOTIFY = {
    "warn": ("Warning from the moderation team",
             "You've received a warning. Reason: {reason}"),
    "mute": ("You've been temporarily muted",
             "You can't post for {hours}h. Reason: {reason}"),
    "suspend": ("Your account has been suspended",
                "Reason: {reason}. Contact the organizers to appeal."),
    "unmute": ("Your mute has been lifted", "You can post again. {reason}"),
    "unsuspend": ("Your account has been reinstated", "Welcome back. {reason}"),
}
_AUDIT = {
    "warn": AuditAction.user_warn,
    "mute": AuditAction.user_mute,
    "suspend": AuditAction.user_suspend,
    "unmute": AuditAction.user_activate,
    "unsuspend": AuditAction.user_activate,
}


def apply_action(db: Session, actor: User, target: User, action: str,
                 reason: str, request: Optional[Request] = None) -> ModerationAction:
    """Apply a moderation action to ``target`` and record/notify/log it."""
    reason = (reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="A reason is required")

    hours = get_settings().MUTE_HOURS
    expires_at = None
    if action == "warn":
        pass
    elif action == "mute":
        expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
        target.muted_until = expires_at
    elif action == "suspend":
        target.is_active = False
    elif action == "unmute":
        target.muted_until = None
    elif action == "unsuspend":
        target.is_active = True
    else:
        raise HTTPException(status_code=422, detail="Unknown moderation action")

    rec = ModerationAction(user_id=target.id, actor_id=actor.id, action=action,
                           reason=reason, expires_at=expires_at)
    db.add(rec)

    title, body_tmpl = _NOTIFY[action]
    db.add(UserNotification(
        user_id=target.id, schedule_item_id=None, kind="moderation",
        title=title, body=body_tmpl.format(reason=reason, hours=hours)))

    log_action(db, actor, _AUDIT[action],
               f"{action.title()} user #{target.id} ({target.email}): {reason}",
               request=request, target=target, target_type="user")
    db.commit()
    db.refresh(rec)
    return rec


# ─── Auto-flagging ───────────────────────────────────────────────
def auto_flag(db: Session, content_type: str, content_id: int, text: str) -> Optional[ContentReport]:
    """If ``text`` trips the filter, create (once) a system report for it."""
    from app.core.moderation_filter import scan, describe
    reasons = scan(text)
    if not reasons:
        return None
    existing = db.query(ContentReport).filter(
        ContentReport.content_type == content_type,
        ContentReport.content_id == content_id,
        ContentReport.source == "auto").first()
    if existing:
        return existing
    rep = ContentReport(content_type=content_type, content_id=content_id,
                        reporter_id=None, source="auto", reason=describe(reasons),
                        status="open")
    db.add(rep)
    db.commit()
    return rep
