"""
Content moderation API.

- Any authenticated attendee can **report** a Q&A question or a poster comment.
- **Moderators** (moderator / admin / super_admin) triage the queue and either
  **remove** the content (deletes it + resolves every report on it) or
  **dismiss** the reports (content stays).

Content types are pluggable via `_RESOLVERS`: each knows how to preview and
remove one kind of content, so adding a new reportable type is one entry.
"""
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.admin_security import is_moderator, can_manage_user
from app.core.audit_service import log_action
from app.core import moderation_service as mod
from app.models.user import User
from app.models.audit_log import AuditAction
from app.models.report import ContentReport
from app.models.moderation import ModerationAction
from app.schemas.moderation import (
    ReportCreate, ReportItem, ReportListResponse, ResolveRequest, ModStats,
    PendingPoster, ApprovalRequest, UserActionRequest, EscalateRequest,
    ModActionItem, UserModerationState,
)

router = APIRouter()


# ─── Content resolvers (preview + remove per content type) ───────
def _q_preview(db: Session, cid: int) -> Optional[dict]:
    from app.models.qa import Question
    from app.models.schedule import ScheduleItem
    q = db.query(Question).filter(Question.id == cid).first()
    if not q:
        return None
    session = db.query(ScheduleItem.title).filter(
        ScheduleItem.id == q.schedule_item_id).scalar()
    author = db.query(User.full_name).filter(User.id == q.user_id).scalar()
    return {"preview": q.text, "context": f"Q&A · {session or 'session'}",
            "author": author, "author_id": q.user_id}


def _q_remove(db: Session, cid: int) -> bool:
    from app.models.qa import Question
    q = db.query(Question).filter(Question.id == cid).first()
    if not q:
        return False
    db.delete(q)
    return True


def _pc_preview(db: Session, cid: int) -> Optional[dict]:
    from app.models.poster import PosterComment, Poster
    c = db.query(PosterComment).filter(PosterComment.id == cid).first()
    if not c:
        return None
    title = db.query(Poster.title).filter(Poster.id == c.poster_id).scalar()
    author = db.query(User.full_name).filter(User.id == c.user_id).scalar()
    return {"preview": c.body, "context": f"Poster · {title or 'poster'}",
            "author": author, "author_id": c.user_id}


def _pc_remove(db: Session, cid: int) -> bool:
    from app.models.poster import PosterComment
    c = db.query(PosterComment).filter(PosterComment.id == cid).first()
    if not c:
        return False
    db.delete(c)
    return True


_RESOLVERS: Dict[str, Dict[str, Callable]] = {
    "question": {"preview": _q_preview, "remove": _q_remove},
    "poster_comment": {"preview": _pc_preview, "remove": _pc_remove},
}


def _require_mod(user: User) -> None:
    if not is_moderator(user):
        raise HTTPException(status_code=403, detail="Moderators only")


# ─── Report (any authenticated user) ─────────────────────────────
@router.post("/report", status_code=201)
def report_content(req: ReportCreate, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    resolver = _RESOLVERS.get(req.content_type)
    if not resolver or resolver["preview"](db, req.content_id) is None:
        raise HTTPException(status_code=404, detail="That content no longer exists")
    existing = db.query(ContentReport).filter(
        ContentReport.content_type == req.content_type,
        ContentReport.content_id == req.content_id,
        ContentReport.reporter_id == user.id,
    ).first()
    if existing:
        return {"message": "Already reported", "id": existing.id}
    r = ContentReport(content_type=req.content_type, content_id=req.content_id,
                      reporter_id=user.id, reason=req.reason, status="open")
    db.add(r)
    db.commit()
    return {"message": "Reported. Thanks — a moderator will review it.", "id": r.id}


# ─── Queue (moderators) ──────────────────────────────────────────
@router.get("/reports", response_model=ReportListResponse)
def list_reports(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _require_mod(user)
    rows = db.query(ContentReport).filter(
        ContentReport.status == "open").order_by(ContentReport.created_at.desc()).all()

    # Group open reports by the content they target.
    groups: Dict[tuple, List[ContentReport]] = {}
    for r in rows:
        groups.setdefault((r.content_type, r.content_id), []).append(r)

    items: List[ReportItem] = []
    orphaned = False
    for (ctype, cid), reps in groups.items():
        resolver = _RESOLVERS.get(ctype)
        info = resolver["preview"](db, cid) if resolver else None
        if info is None:
            # Content already gone — auto-resolve these stale reports.
            for rr in reps:
                rr.status = "resolved"
                rr.action_taken = "gone"
            orphaned = True
            continue
        reasons = [rr.reason for rr in reps if rr.reason]
        items.append(ReportItem(
            id=min(rr.id for rr in reps), content_type=ctype, content_id=cid,
            content_preview=info["preview"], content_context=info["context"],
            content_author=info["author"], content_author_id=info.get("author_id"),
            reasons=reasons, report_count=len(reps),
            auto_flagged=any(rr.source == "auto" for rr in reps),
            first_reported_at=min(rr.created_at for rr in reps),
        ))
    if orphaned:
        db.commit()
    items.sort(key=lambda i: (i.report_count, i.first_reported_at), reverse=True)
    return ReportListResponse(reports=items, open_count=len(items))


@router.get("/queue-count")
def queue_count(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not is_moderator(user):
        return {"count": 0}
    # Distinct reported items still open.
    rows = db.query(ContentReport.content_type, ContentReport.content_id).filter(
        ContentReport.status == "open").distinct().all()
    return {"count": len(rows)}


@router.get("/stats", response_model=ModStats)
def mod_stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _require_mod(user)
    from sqlalchemy import func
    counts = dict(db.query(ContentReport.status, func.count(ContentReport.id))
                  .group_by(ContentReport.status).all())
    by_type = dict(db.query(ContentReport.content_type, func.count(ContentReport.id))
                   .filter(ContentReport.status == "open")
                   .group_by(ContentReport.content_type).all())
    from app.models.poster import Poster
    pending = db.query(func.count(Poster.id)).filter(Poster.status == "pending").scalar() or 0
    return ModStats(open=counts.get("open", 0), resolved=counts.get("resolved", 0),
                    dismissed=counts.get("dismissed", 0), pending_posters=pending,
                    by_type=by_type)


@router.post("/reports/{report_id}/resolve")
def resolve_report(report_id: int, req: ResolveRequest, request: Request,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _require_mod(user)
    rep = db.query(ContentReport).filter(ContentReport.id == report_id).first()
    if not rep:
        raise HTTPException(status_code=404, detail="Report not found")

    # Act on every open report for the same content.
    siblings = db.query(ContentReport).filter(
        ContentReport.content_type == rep.content_type,
        ContentReport.content_id == rep.content_id,
        ContentReport.status == "open",
    ).all()

    resolver = _RESOLVERS.get(rep.content_type)
    # Capture the author before removal so we can notify them.
    author_id = None
    if resolver:
        prev = resolver["preview"](db, rep.content_id)
        if prev:
            author_id = prev.get("author_id")

    removed = False
    if req.action == "remove":
        removed = bool(resolver and resolver["remove"](db, rep.content_id))
        action, new_status = "removed", "resolved"
    else:
        action, new_status = "dismissed", "dismissed"

    # Close the loop: tell the content author when their content was removed,
    # and tell each reporter their report was handled.
    from app.models.notification import UserNotification
    label = {"question": "question", "poster_comment": "poster comment"}.get(
        rep.content_type, "post")
    if removed and author_id:
        db.add(UserNotification(
            user_id=author_id, schedule_item_id=None, kind="moderation",
            title="A post was removed",
            body=f"Your {label} was removed by a moderator" +
                 (f": {req.reason}" if req.reason else ".")))
    reporter_ids = {s.reporter_id for s in siblings if getattr(s, "reporter_id", None)}
    for rid in reporter_ids:
        db.add(UserNotification(
            user_id=rid, schedule_item_id=None, kind="moderation",
            title="Thanks — your report was reviewed",
            body=f"The {label} you reported was {action} by our moderators."))

    now = datetime.now(timezone.utc)
    for s in siblings:
        s.status = new_status
        s.action_taken = action
        s.resolved_by = user.id
        s.resolved_at = now

    detail = (f"{action.title()} {rep.content_type} #{rep.content_id} "
              f"({len(siblings)} report(s))")
    if req.reason:
        detail += f" — {req.reason}"
    log_action(
        db, user,
        AuditAction.content_remove if req.action == "remove" else AuditAction.content_approve,
        detail, request=request, target_type=rep.content_type,
        target_id=rep.content_id,
    )
    db.commit()
    return {"action": action, "removed": removed, "reports_closed": len(siblings)}


# ─── Poster upload-approval workflow (moderators) ────────────────
def _poster_or_404(db: Session, poster_id: int):
    from app.models.poster import Poster
    p = db.query(Poster).filter(Poster.id == poster_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Poster not found")
    return p


@router.get("/posters", response_model=List[PendingPoster])
def pending_posters(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _require_mod(user)
    from app.models.poster import Poster
    rows = db.query(Poster).filter(Poster.status == "pending").order_by(
        Poster.created_at.asc()).all()
    out = []
    for p in rows:
        presenter = db.query(User.full_name).filter(
            User.id == p.presenter_id).scalar() if p.presenter_id else None
        out.append(PendingPoster(
            id=p.id, title=p.title, authors=p.authors, category=p.category,
            abstract=p.abstract, presenter_name=presenter,
            has_image=bool(p.stored_image), created_at=p.created_at))
    return out


def _notify_poster_owner(db: Session, poster, title: str, body: str) -> None:
    from app.models.notification import UserNotification
    owner = poster.presenter_id or poster.created_by
    if owner:
        db.add(UserNotification(user_id=owner, schedule_item_id=None,
                                kind="poster", title=title, body=body))


@router.post("/posters/{poster_id}/approve")
def approve_poster(poster_id: int, req: ApprovalRequest, request: Request,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _require_mod(user)
    p = _poster_or_404(db, poster_id)
    p.status = "approved"
    _notify_poster_owner(db, p, "Poster approved",
                         f"Your poster \"{p.title[:60]}\" is now live in the gallery.")
    log_action(db, user, AuditAction.content_approve,
               f"Approved poster #{p.id}" + (f" — {req.reason}" if req.reason else ""),
               request=request, target_type="poster", target_id=p.id)
    db.commit()
    return {"status": "approved", "id": p.id}


@router.post("/posters/{poster_id}/reject")
def reject_poster(poster_id: int, req: ApprovalRequest, request: Request,
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _require_mod(user)
    p = _poster_or_404(db, poster_id)
    p.status = "rejected"
    reason = req.reason or "It didn't meet the gallery guidelines."
    _notify_poster_owner(db, p, "Poster not approved",
                         f"Your poster \"{p.title[:60]}\" wasn't approved. {reason}")
    log_action(db, user, AuditAction.content_reject,
               f"Rejected poster #{p.id} — {reason}",
               request=request, target_type="poster", target_id=p.id)
    db.commit()
    return {"status": "rejected", "id": p.id}


# ─── User escalation ladder: warn → 24h mute → suspend ───────────
def _target_or_404(db: Session, user_id: int) -> User:
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    return u


def _state(db: Session, target: User, applied: Optional[str] = None) -> UserModerationState:
    hist = db.query(ModerationAction).filter(
        ModerationAction.user_id == target.id).order_by(
        ModerationAction.created_at.desc()).all()
    return UserModerationState(
        user_id=target.id, name=target.full_name, email=target.email,
        is_active=target.is_active, muted=mod.is_muted(target),
        muted_until=target.muted_until, next_step=mod.next_escalation(db, target),
        applied=applied,
        history=[ModActionItem(id=h.id, action=h.action, reason=h.reason,
                               actor_id=h.actor_id, expires_at=h.expires_at,
                               created_at=h.created_at) for h in hist])


@router.get("/users/{user_id}/moderation", response_model=UserModerationState)
def user_moderation(user_id: int, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    _require_mod(user)
    return _state(db, _target_or_404(db, user_id))


@router.post("/users/{user_id}/escalate", response_model=UserModerationState)
def escalate_user(user_id: int, req: EscalateRequest, request: Request,
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Apply the next rung of the ladder (warn → mute → suspend)."""
    _require_mod(user)
    target = _target_or_404(db, user_id)
    if not can_manage_user(user, target):
        raise HTTPException(status_code=403,
                            detail="You can't take action on this user")
    step = mod.next_escalation(db, target)
    if step is None:
        raise HTTPException(status_code=400, detail="User is already suspended")
    mod.apply_action(db, user, target, step, req.reason, request=request)
    db.refresh(target)
    return _state(db, target, applied=step)


@router.post("/users/{user_id}/action", response_model=UserModerationState)
def user_action(user_id: int, req: UserActionRequest, request: Request,
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Apply a specific action (warn / mute / suspend / unmute / unsuspend)."""
    _require_mod(user)
    target = _target_or_404(db, user_id)
    if not can_manage_user(user, target):
        raise HTTPException(status_code=403,
                            detail="You can't take action on this user")
    mod.apply_action(db, user, target, req.action, req.reason, request=request)
    db.refresh(target)
    return _state(db, target, applied=req.action)
