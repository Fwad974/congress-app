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
from app.core.admin_security import is_moderator
from app.core.audit_service import log_action
from app.models.user import User
from app.models.audit_log import AuditAction
from app.models.report import ContentReport
from app.schemas.moderation import (
    ReportCreate, ReportItem, ReportListResponse, ResolveRequest, ModStats,
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
    return {"preview": q.text, "context": f"Q&A · {session or 'session'}", "author": author}


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
    return {"preview": c.body, "context": f"Poster · {title or 'poster'}", "author": author}


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
            content_author=info["author"], reasons=reasons, report_count=len(reps),
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
    return ModStats(open=counts.get("open", 0), resolved=counts.get("resolved", 0),
                    dismissed=counts.get("dismissed", 0), by_type=by_type)


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

    removed = False
    if req.action == "remove":
        resolver = _RESOLVERS.get(rep.content_type)
        removed = bool(resolver and resolver["remove"](db, rep.content_id))
        action, new_status = "removed", "resolved"
    else:
        action, new_status = "dismissed", "dismissed"

    now = datetime.now(timezone.utc)
    for s in siblings:
        s.status = new_status
        s.action_taken = action
        s.resolved_by = user.id
        s.resolved_at = now

    log_action(
        db, user,
        AuditAction.content_remove if req.action == "remove" else AuditAction.content_approve,
        f"{action.title()} {rep.content_type} #{rep.content_id} "
        f"({len(siblings)} report(s))",
        request=request, target_type=rep.content_type, target_id=rep.content_id,
    )
    db.commit()
    return {"action": action, "removed": removed, "reports_closed": len(siblings)}
