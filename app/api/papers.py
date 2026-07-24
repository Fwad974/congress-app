"""
Abstracts & Paper submission + peer review API.

Roles:
- **Author** (any authenticated user): submit, track status, respond to
  reviewer comments, resubmit revisions.
- **Reviewer** (role `reviewer`/`review_chair`): review papers assigned to them.
- **Review chair** (`review_chair`/`admin`/`super_admin`): list all, assign
  reviewers (with same-institution COI guard), and decide.

Reviews stay hidden from the author until a decision; the submitter's identity
is hidden from reviewers (light double-blind).
"""
import os
from datetime import datetime, timezone, date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc, func

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.core.admin_security import is_review_chair, is_assignable_reviewer
from app.core import paper_files
from app.core.audit_service import log_action
from app.models.user import User
from app.models.audit_log import AuditAction
from app.models.notification import UserNotification
from app.models.paper import Paper, Review, PaperStatus, MAX_REVISION_ROUNDS
from app.schemas.paper import (
    PaperCreate, PaperUpdate, ReviewUpsert, ReviewRespond, AssignRequest,
    DecisionRequest, DeadlineRequest, PaperResponse, PaperListResponse,
    ReviewResponse, ReviewerInfo, ReviewerLoad, AttentionPaper, ReviewChairOverview,
)

router = APIRouter()

_DECIDED = {PaperStatus.revision_requested, PaperStatus.accepted, PaperStatus.rejected}
# Reviewers may only act while the paper is actively in review.
_REVIEWABLE = {PaperStatus.submitted, PaperStatus.under_review}
# Terminal states — no further assignment or decisions.
_TERMINAL = {PaperStatus.accepted, PaperStatus.rejected, PaperStatus.withdrawn}


def _notify(db: Session, user_id: int, title: str, body: str) -> None:
    db.add(UserNotification(user_id=user_id, schedule_item_id=None,
                            kind="paper", title=title, body=body))


def _notify_review_chairs(db: Session, title: str, body: str) -> None:
    """Fan a note out to everyone who can manage reviews (review chairs)."""
    from app.models.user import UserRole
    chairs = db.query(User.id).filter(
        User.role == UserRole.review_chair, User.is_active == True  # noqa: E712
    ).all()
    for (cid,) in chairs:
        _notify(db, cid, title, body)


def _assigned_review(db: Session, paper_id: int, user_id: int) -> Optional[Review]:
    return db.query(Review).filter(
        Review.paper_id == paper_id, Review.reviewer_id == user_id
    ).first()


def _is_overdue(paper: Paper) -> bool:
    """Under review, past its deadline — the chair should chase reviewers."""
    return bool(paper.review_deadline
                and paper.status == PaperStatus.under_review
                and paper.review_deadline < date.today())


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s.strip()[:10])
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date (use YYYY-MM-DD)")


def _serialize(db: Session, paper: Paper, viewer: User) -> PaperResponse:
    is_mine = paper.author_id == viewer.id
    can_manage = is_review_chair(viewer)

    reviews = db.query(Review).filter(Review.paper_id == paper.id).all()
    submitted = [r for r in reviews if r.submitted]
    scores = [r.score for r in submitted if r.score is not None]
    avg = round(sum(scores) / len(scores), 2) if scores else None

    author_name = None
    if is_mine or can_manage:
        author = db.query(User.full_name).filter(User.id == paper.author_id).scalar()
        author_name = author

    my_review = None
    review_out: List[ReviewResponse] = []

    if can_manage:
        names = {u.id: u.full_name for u in db.query(User).filter(
            User.id.in_([r.reviewer_id for r in reviews] or [0])).all()}
        review_out = [ReviewResponse(
            id=r.id, reviewer_label=names.get(r.reviewer_id, "Reviewer"),
            reviewer_id=r.reviewer_id, score=r.score, comments=r.comments,
            submitted=r.submitted, state=r.state, response_reason=r.response_reason,
            updated_at=r.updated_at,
        ) for r in reviews]
    elif is_mine and paper.status in _DECIDED:
        # Author sees anonymized, submitted reviews only, after a decision.
        review_out = [ReviewResponse(
            id=r.id, reviewer_label=f"Reviewer {i + 1}", score=r.score,
            comments=r.comments, submitted=True, updated_at=r.updated_at,
        ) for i, r in enumerate(submitted)]
    else:
        mine = next((r for r in reviews if r.reviewer_id == viewer.id), None)
        if mine:
            my_review = ReviewResponse(
                id=mine.id, reviewer_label="You", score=mine.score,
                comments=mine.comments, submitted=mine.submitted,
                state=mine.state, response_reason=mine.response_reason,
                updated_at=mine.updated_at,
            )

    # Blind review: hide the free-text author list from reviewers (anyone who
    # isn't the author or a chair) while the paper is under review. Accepted
    # papers are public (the proceedings showcase), so authors show there.
    blind = not (is_mine or can_manage) and paper.status != PaperStatus.accepted
    authors_out = "(hidden for blind review)" if blind else paper.authors

    # Score aggregates are for the chair always, and the author only after a
    # decision — never leak the running average to the author mid-review or to
    # a reviewer (who must stay blind to peers).
    show_aggregates = can_manage or (is_mine and paper.status in _DECIDED)
    return PaperResponse(
        id=paper.id, title=paper.title, authors=authors_out,
        category=paper.category, abstract=paper.abstract, file_url=paper.file_url,
        file_name=paper.file_name, has_file=bool(paper.stored_file),
        status=paper.status.value, round=paper.round,
        author_response=paper.author_response, decision_comment=paper.decision_comment,
        author_name=author_name, is_mine=is_mine, can_manage=can_manage,
        review_count=len(reviews) if show_aggregates else 0,
        submitted_review_count=len(submitted) if show_aggregates else 0,
        avg_score=avg if show_aggregates else None,
        review_deadline=paper.review_deadline.isoformat() if paper.review_deadline else None,
        is_overdue=_is_overdue(paper),
        my_review=my_review, reviews=review_out,
        created_at=paper.created_at, updated_at=paper.updated_at,
    )


# ─── Author ──────────────────────────────────────────────────────
@router.post("", response_model=PaperResponse, status_code=201)
@router.post("/", response_model=PaperResponse, status_code=201)
def submit_paper(req: PaperCreate, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    paper = Paper(
        author_id=user.id, title=req.title, authors=req.authors,
        category=req.category, abstract=req.abstract, file_url=req.file_url,
        status=PaperStatus.submitted, round=1,
    )
    db.add(paper)
    db.commit()
    db.refresh(paper)
    return _serialize(db, paper, user)


@router.get("/mine", response_model=PaperListResponse)
def my_papers(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    papers = db.query(Paper).filter(Paper.author_id == user.id).order_by(
        desc(Paper.created_at)).all()
    return PaperListResponse(papers=[_serialize(db, p, user) for p in papers],
                             total=len(papers), can_manage=is_review_chair(user))


@router.get("/assigned", response_model=PaperListResponse)
def assigned_papers(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    paper_ids = [r.paper_id for r in db.query(Review.paper_id).filter(
        Review.reviewer_id == user.id).all()]
    papers = (db.query(Paper).filter(Paper.id.in_(paper_ids)).order_by(desc(Paper.created_at)).all()
              if paper_ids else [])
    return PaperListResponse(papers=[_serialize(db, p, user) for p in papers],
                             total=len(papers), can_manage=is_review_chair(user))


# ─── Accepted papers showcase (all attendees) ────────────────────
@router.get("/accepted", response_model=PaperListResponse)
def accepted_papers(category: Optional[str] = Query(None),
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """Public list of accepted papers — the in-app conference proceedings.

    Any authenticated attendee can browse these. _serialize returns a
    public-safe view for a non-author/non-chair viewer (no reviews, no
    internal reviewer/author-identity leaks).
    """
    q = db.query(Paper).filter(Paper.status == PaperStatus.accepted)
    if category:
        q = q.filter(Paper.category == category)
    papers = q.order_by(Paper.category, Paper.title).all()
    return PaperListResponse(papers=[_serialize(db, p, user) for p in papers],
                             total=len(papers), can_manage=is_review_chair(user))


# ─── Review chair: list all + assignable reviewers ───────────────
@router.get("", response_model=PaperListResponse)
@router.get("/", response_model=PaperListResponse)
def list_papers(
    status: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not is_review_chair(user):
        raise HTTPException(status_code=403, detail="Not allowed")
    q = db.query(Paper)
    if status:
        try:
            q = q.filter(Paper.status == PaperStatus(status))
        except ValueError:
            pass
    if category:
        q = q.filter(Paper.category == category)
    papers = q.order_by(desc(Paper.created_at)).all()
    return PaperListResponse(papers=[_serialize(db, p, user) for p in papers],
                             total=len(papers), can_manage=True)


@router.get("/reviewers", response_model=List[ReviewerInfo])
def assignable_reviewers(
    paper_id: int = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not is_review_chair(user):
        raise HTTPException(status_code=403, detail="Not allowed")
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    author = db.query(User).filter(User.id == paper.author_id).first()
    author_inst = (author.institution or "").strip().lower() if author else ""
    state_by_reviewer = {r.reviewer_id: r.state for r in db.query(
        Review.reviewer_id, Review.state).filter(Review.paper_id == paper_id).all()}

    from app.core.admin_security import ASSIGNABLE_REVIEWER_ROLES
    reviewers = db.query(User).filter(
        User.role.in_(ASSIGNABLE_REVIEWER_ROLES), User.is_active == True  # noqa: E712
    ).all()
    active_load, completed_load = _reviewer_loads(db)
    out = []
    for r in reviewers:
        if r.id == paper.author_id:
            continue   # never review your own paper
        inst = (r.institution or "").strip().lower()
        out.append(ReviewerInfo(
            id=r.id, full_name=r.full_name, email=r.email, institution=r.institution,
            coi=bool(author_inst and inst == author_inst),
            assigned=r.id in state_by_reviewer,
            state=state_by_reviewer.get(r.id),
            active_load=active_load.get(r.id, 0),
            completed_load=completed_load.get(r.id, 0),
        ))
    return out


def _reviewer_loads(db: Session):
    """Per-reviewer workload across all papers: (active dict, completed dict).

    active   = assignments accepted/invited and not yet submitted.
    completed = reviews submitted.
    """
    active: dict = {}
    completed: dict = {}
    rows = db.query(Review.reviewer_id, Review.submitted, Review.state).all()
    for rid, submitted, state in rows:
        if submitted:
            completed[rid] = completed.get(rid, 0) + 1
        elif state not in ("declined", "recused"):
            active[rid] = active.get(rid, 0) + 1
    return active, completed


# ─── Review-chair dashboard ──────────────────────────────────────
MIN_REVIEWERS = 2  # REV-01


@router.get("/overview", response_model=ReviewChairOverview)
def review_overview(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not is_review_chair(user):
        raise HTTPException(status_code=403, detail="Not allowed")

    papers = db.query(Paper).all()
    reviews = db.query(Review).all()
    by_paper: dict = {}
    for r in reviews:
        by_paper.setdefault(r.paper_id, []).append(r)

    status_counts: dict = {}
    reviews_assigned = reviews_submitted = 0
    for r in reviews:
        if r.submitted:
            reviews_submitted += 1
        if r.state not in ("declined", "recused"):
            reviews_assigned += 1

    def _att(p: Paper) -> AttentionPaper:
        rs = by_paper.get(p.id, [])
        active = [r for r in rs if r.state not in ("declined", "recused")]
        return AttentionPaper(
            id=p.id, title=p.title, status=p.status.value, category=p.category,
            review_count=len(active),
            submitted_review_count=len([r for r in active if r.submitted]),
            review_deadline=p.review_deadline.isoformat() if p.review_deadline else None,
            is_overdue=_is_overdue(p),
        )

    needs_assignment, awaiting_decision, overdue = [], [], []
    for p in papers:
        status_counts[p.status.value] = status_counts.get(p.status.value, 0) + 1
        rs = by_paper.get(p.id, [])
        active = [r for r in rs if r.state not in ("declined", "recused")]
        submitted = [r for r in active if r.submitted]
        if p.status in (PaperStatus.submitted, PaperStatus.under_review) and len(active) < MIN_REVIEWERS:
            needs_assignment.append(_att(p))
        if p.status == PaperStatus.under_review and active and len(submitted) == len(active):
            awaiting_decision.append(_att(p))
        if _is_overdue(p):
            overdue.append(_att(p))

    from app.core.admin_security import ASSIGNABLE_REVIEWER_ROLES
    active_load, completed_load = _reviewer_loads(db)
    roster = db.query(User).filter(
        User.role.in_(ASSIGNABLE_REVIEWER_ROLES), User.is_active == True  # noqa: E712
    ).order_by(User.full_name).all()
    reviewers = [ReviewerLoad(
        id=u.id, full_name=u.full_name, email=u.email,
        active=active_load.get(u.id, 0), completed=completed_load.get(u.id, 0),
    ) for u in roster]

    return ReviewChairOverview(
        total=len(papers), status_counts=status_counts,
        reviews_assigned=reviews_assigned, reviews_submitted=reviews_submitted,
        reviews_pending=reviews_assigned - reviews_submitted,
        needs_assignment=needs_assignment, awaiting_decision=awaiting_decision,
        overdue=overdue, reviewers=reviewers,
    )


# ─── Single paper (author / assigned reviewer / chair) ───────────
@router.get("/{paper_id}", response_model=PaperResponse)
def get_paper(paper_id: int, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    allowed = (paper.author_id == user.id or is_review_chair(user)
               or _assigned_review(db, paper_id, user.id) is not None)
    if not allowed:
        raise HTTPException(status_code=403, detail="Not allowed")
    return _serialize(db, paper, user)


@router.put("/{paper_id}", response_model=PaperResponse)
def update_paper(paper_id: int, req: PaperUpdate, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    if paper.author_id != user.id:
        raise HTTPException(status_code=403, detail="Only the author can edit this")
    if paper.status not in (PaperStatus.submitted, PaperStatus.revision_requested):
        raise HTTPException(status_code=400, detail="This paper can't be edited now")

    for f in ("title", "authors", "abstract", "category", "file_url"):
        val = getattr(req, f)
        if val is not None:
            setattr(paper, f, val.strip() if isinstance(val, str) else val)

    resubmitting = paper.status == PaperStatus.revision_requested
    if resubmitting:
        paper.author_response = req.author_response
        paper.round += 1
        paper.status = PaperStatus.under_review
        paper.decision_comment = None
        # Reviewers re-evaluate the revised version.
        db.query(Review).filter(Review.paper_id == paper.id).update(
            {Review.submitted: False}, synchronize_session=False)
    paper.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(paper)
    return _serialize(db, paper, user)


@router.post("/{paper_id}/withdraw", response_model=PaperResponse)
def withdraw_paper(paper_id: int, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    if paper.author_id != user.id:
        raise HTTPException(status_code=403, detail="Only the author can withdraw this")
    if paper.status in (PaperStatus.accepted, PaperStatus.rejected, PaperStatus.withdrawn):
        raise HTTPException(status_code=400, detail="This paper can't be withdrawn now")
    paper.status = PaperStatus.withdrawn
    paper.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(paper)
    return _serialize(db, paper, user)


# ─── Manuscript file (PDF / Word) ────────────────────────────────
def _can_view(db: Session, paper: Paper, user: User) -> bool:
    return (paper.author_id == user.id or is_review_chair(user)
            or _assigned_review(db, paper.id, user.id) is not None)


@router.post("/{paper_id}/file", response_model=PaperResponse)
async def upload_paper_file(paper_id: int, file: UploadFile = File(...),
                            db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    if paper.author_id != user.id:
        raise HTTPException(status_code=403, detail="Only the author can upload a file")
    if paper.status not in (PaperStatus.submitted, PaperStatus.revision_requested):
        raise HTTPException(status_code=400, detail="This paper can't be edited now")
    if not paper_files.is_allowed(file.filename or ""):
        raise HTTPException(status_code=422,
                            detail=f"Only {paper_files.EXT_LABEL} files are allowed")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="The file is empty")
    if len(data) > paper_files.max_bytes():
        raise HTTPException(
            status_code=413,
            detail=f"File is too large (max {get_settings().MAX_UPLOAD_MB} MB)")

    old = paper.stored_file
    paper.stored_file = paper_files.save_bytes(data, file.filename)
    paper.file_name = (file.filename or "manuscript")[:255]
    paper.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(paper)
    if old:
        paper_files.delete_file(old)   # replace: drop the previous upload
    return _serialize(db, paper, user)


@router.get("/{paper_id}/file")
def download_paper_file(paper_id: int, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    # Accepted papers are part of the public proceedings — any attendee may
    # download them; otherwise only the author, an assigned reviewer, or a chair.
    if paper.status != PaperStatus.accepted and not _can_view(db, paper, user):
        raise HTTPException(status_code=403, detail="Not allowed")
    if not paper.stored_file:
        raise HTTPException(status_code=404, detail="No file uploaded")
    path = paper_files.path_for(paper.stored_file)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File is missing")
    return FileResponse(path, filename=paper.file_name or "manuscript",
                        media_type="application/octet-stream")


@router.delete("/{paper_id}/file", response_model=PaperResponse)
def delete_paper_file(paper_id: int, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    if paper.author_id != user.id:
        raise HTTPException(status_code=403, detail="Only the author can remove the file")
    if paper.status not in (PaperStatus.submitted, PaperStatus.revision_requested):
        raise HTTPException(status_code=400, detail="This paper can't be edited now")
    old = paper.stored_file
    paper.stored_file = None
    paper.file_name = None
    paper.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(paper)
    if old:
        paper_files.delete_file(old)
    return _serialize(db, paper, user)


# ─── Reviewer ────────────────────────────────────────────────────
@router.put("/{paper_id}/review", response_model=PaperResponse)
def upsert_review(paper_id: int, req: ReviewUpsert, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    review = _assigned_review(db, paper_id, user.id)
    if not review:
        raise HTTPException(status_code=403, detail="You're not assigned to review this")
    if paper.status not in _REVIEWABLE:
        raise HTTPException(status_code=400, detail="Reviewing is closed for this paper")
    if review.state in ("declined", "recused"):
        raise HTTPException(
            status_code=400,
            detail=f"You {review.state} this assignment — you can't review it.")
    if req.score is not None:
        review.score = req.score
    if req.comments is not None:
        review.comments = req.comments
    review.submitted = req.submitted
    if review.state == "invited":
        review.state = "accepted"   # engaging with the review implies acceptance
    review.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(paper)
    return _serialize(db, paper, user)


@router.post("/{paper_id}/respond", response_model=PaperResponse)
def respond_to_assignment(paper_id: int, req: ReviewRespond,
                          db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    """A reviewer accepts, declines, or recuses from an assignment."""
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    review = _assigned_review(db, paper_id, user.id)
    if not review:
        raise HTTPException(status_code=403, detail="You're not assigned to review this")
    if paper.status not in _REVIEWABLE:
        raise HTTPException(status_code=400, detail="This assignment is closed")

    if req.action == "accept":
        review.state = "accepted"
        review.response_reason = None
    else:  # decline | recuse
        review.state = "declined" if req.action == "decline" else "recused"
        review.response_reason = (req.reason or "").strip() or None
        review.submitted = False       # withdraw any in-progress review
        verb = "declined" if req.action == "decline" else "recused themselves from"
        _notify_review_chairs(
            db, "Reviewer response",
            f"A reviewer {verb} a submission ({paper.category or 'paper'})."
            + (f" Reason: {review.response_reason}" if review.response_reason else ""))
    review.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(paper)
    return _serialize(db, paper, user)


# ─── Review chair: assign + decide ───────────────────────────────
@router.post("/{paper_id}/assign", response_model=PaperResponse)
def assign_reviewers(paper_id: int, req: AssignRequest, request: Request,
                     db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not is_review_chair(user):
        raise HTTPException(status_code=403, detail="Not allowed")
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    if paper.status in _TERMINAL:
        raise HTTPException(status_code=400,
                            detail="This paper is closed — you can't assign reviewers")

    author = db.query(User).filter(User.id == paper.author_id).first()
    author_inst = (author.institution or "").strip().lower() if author else ""

    reviewers = db.query(User).filter(User.id.in_(req.reviewer_ids or [])).all()
    reviewers = [r for r in reviewers if is_assignable_reviewer(r) and r.id != paper.author_id]
    if not reviewers:
        raise HTTPException(status_code=400, detail="No valid reviewers selected")

    # COI (REV-02): block same-institution unless the chair overrides.
    if not req.override_coi and author_inst:
        conflicts = [r.full_name for r in reviewers
                     if (r.institution or "").strip().lower() == author_inst]
        if conflicts:
            raise HTTPException(
                status_code=400,
                detail=f"Conflict of interest (same institution): {', '.join(conflicts)}. "
                       "Re-submit with override to assign anyway.")

    existing = {r.reviewer_id for r in db.query(Review.reviewer_id).filter(
        Review.paper_id == paper_id).all()}
    new_ids = []
    for r in reviewers:
        if r.id in existing:
            continue
        db.add(Review(paper_id=paper_id, reviewer_id=r.id))
        new_ids.append(r.id)
        _notify(db, r.id, "New paper to review",
                f"You've been assigned to review a submission ({paper.category or 'paper'}).")

    if paper.status == PaperStatus.submitted:
        paper.status = PaperStatus.under_review
    if req.deadline is not None:
        paper.review_deadline = _parse_date(req.deadline)
    paper.updated_at = datetime.now(timezone.utc)
    log_action(db, user, AuditAction.paper_decision,
               f"Assigned {len(new_ids)} reviewer(s) to paper #{paper.id}",
               request=request, target_type="paper", target_id=paper.id)
    db.refresh(paper)
    return _serialize(db, paper, user)


@router.put("/{paper_id}/deadline", response_model=PaperResponse)
def set_review_deadline(paper_id: int, req: DeadlineRequest,
                        db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Chair sets or clears a paper's review due date."""
    if not is_review_chair(user):
        raise HTTPException(status_code=403, detail="Not allowed")
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    paper.review_deadline = _parse_date(req.deadline)
    paper.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(paper)
    return _serialize(db, paper, user)


@router.delete("/{paper_id}/assign/{reviewer_id}", response_model=PaperResponse)
def unassign_reviewer(paper_id: int, reviewer_id: int,
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Chair removes a reviewer assignment (only if not already submitted)."""
    if not is_review_chair(user):
        raise HTTPException(status_code=403, detail="Not allowed")
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    review = db.query(Review).filter(
        Review.paper_id == paper_id, Review.reviewer_id == reviewer_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="That reviewer isn't assigned")
    if review.submitted:
        raise HTTPException(status_code=400,
                            detail="This reviewer already submitted — their review can't be removed")
    db.delete(review)
    db.flush()   # ensure the removed row isn't counted below
    _notify(db, reviewer_id, "Review assignment removed",
            f"You're no longer assigned to review a submission ({paper.category or 'paper'}).")
    # If no active reviewers remain, drop the paper back to awaiting assignment.
    remaining = db.query(Review).filter(
        Review.paper_id == paper_id,
        Review.state.notin_(("declined", "recused"))).count()
    if remaining == 0 and paper.status == PaperStatus.under_review:
        paper.status = PaperStatus.submitted
    paper.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(paper)
    return _serialize(db, paper, user)


@router.post("/{paper_id}/decision", response_model=PaperResponse)
def decide_paper(paper_id: int, req: DecisionRequest, request: Request,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not is_review_chair(user):
        raise HTTPException(status_code=403, detail="Not allowed")
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    if paper.status in _TERMINAL:
        raise HTTPException(status_code=400,
                            detail="A decision has already been made on this paper")

    # `round` starts at 1 (original submission) and increments on each resubmit,
    # so the number of revisions already granted is `round - 1`. Allow up to
    # MAX_REVISION_ROUNDS revisions before forcing an accept/reject.
    if req.decision == "revision" and (paper.round - 1) >= MAX_REVISION_ROUNDS:
        raise HTTPException(
            status_code=400,
            detail=f"Max {MAX_REVISION_ROUNDS} revision rounds reached — accept or reject.")

    new_status = {
        "accept": PaperStatus.accepted,
        "reject": PaperStatus.rejected,
        "revision": PaperStatus.revision_requested,
    }[req.decision]
    paper.status = new_status
    paper.decision_comment = (req.comment or "").strip() or None
    paper.updated_at = datetime.now(timezone.utc)

    msg = {"accept": "accepted 🎉", "reject": "not accepted",
           "revision": "returned for revision"}[req.decision]
    _notify(db, paper.author_id, f"Decision on \"{paper.title[:60]}\"",
            f"Your submission was {msg}.")
    log_action(db, user, AuditAction.paper_decision,
               f"Decision on paper #{paper.id}: {req.decision}",
               request=request, target_type="paper", target_id=paper.id,
               new_value=new_status.value)
    db.refresh(paper)
    return _serialize(db, paper, user)
