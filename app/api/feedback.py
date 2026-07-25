"""
Feedback & Ratings API.

- **Attendees** leave a 1-tap star rating (+ optional comment, + optional
  private speaker feedback) per session, and complete the post-event survey
  (which unlocks the participation certificate).
- **Speakers** get an anonymized digest of the audience reaction to their own
  sessions (average, distribution, comments — never the private speaker feedback
  or reviewer identities).
- **Organizers** (admin / super_admin) get a real-time sentiment dashboard
  across all sessions plus the survey aggregate, and can read the private
  speaker feedback.
"""
import csv
import io
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User, UserRole
from app.models.schedule import ScheduleItem
from app.models.feedback import SessionRating, SurveyResponse
from app.schemas.feedback import (
    RatingUpsert, RatingResponse, SessionSummary, MyRating,
    SurveyCreate, SurveyResponseOut, SurveyAggregate,
    SentimentDashboard, SpeakerDigest,
)

router = APIRouter()

ORGANIZER_ROLES = {UserRole.admin, UserRole.super_admin}


def _is_organizer(user: User) -> bool:
    return user.role in ORGANIZER_ROLES


def _session_or_404(db: Session, session_id: int) -> ScheduleItem:
    it = db.query(ScheduleItem).filter(ScheduleItem.id == session_id).first()
    if not it:
        raise HTTPException(status_code=404, detail="Session not found")
    return it


def _distribution(rows) -> Dict[int, int]:
    dist = {i: 0 for i in range(1, 6)}
    for r in rows:
        if r.stars in dist:
            dist[r.stars] += 1
    return dist


def _avg(vals) -> float:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def _summary(db: Session, item: ScheduleItem, *, include_private: bool) -> SessionSummary:
    rows = db.query(SessionRating).filter(
        SessionRating.schedule_item_id == item.id).all()
    stars = [r.stars for r in rows]
    comments = [r.comment for r in rows if r.comment]
    summary = SessionSummary(
        session_id=item.id, title=item.title,
        average=_avg(stars), count=len(rows), distribution=_distribution(rows),
        comments=comments,
    )
    if include_private:
        summary.speaker_feedback = [r.speaker_feedback for r in rows if r.speaker_feedback]
    return summary


# ─── Attendee: rate a session ────────────────────────────────────
@router.post("/sessions/{session_id}/rating", response_model=RatingResponse)
def upsert_rating(session_id: int, req: RatingUpsert, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    _session_or_404(db, session_id)
    # A rating carries a free-text comment shown to speakers/organizers, so the
    # mute guard applies (MOD-04).
    if (req.comment or req.speaker_feedback):
        from app.core.moderation_service import ensure_can_post
        ensure_can_post(user)
    row = db.query(SessionRating).filter(
        SessionRating.schedule_item_id == session_id,
        SessionRating.user_id == user.id).first()
    if row:
        row.stars = req.stars
        row.comment = req.comment
        row.speaker_feedback = req.speaker_feedback
        row.updated_at = datetime.now(timezone.utc)
    else:
        row = SessionRating(schedule_item_id=session_id, user_id=user.id,
                            stars=req.stars, comment=req.comment,
                            speaker_feedback=req.speaker_feedback)
        db.add(row)
    db.commit()
    db.refresh(row)
    return RatingResponse(session_id=session_id, stars=row.stars, comment=row.comment,
                          speaker_feedback=row.speaker_feedback, rated=True,
                          updated_at=row.updated_at)


@router.get("/sessions/{session_id}/rating", response_model=RatingResponse)
def my_rating(session_id: int, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    row = db.query(SessionRating).filter(
        SessionRating.schedule_item_id == session_id,
        SessionRating.user_id == user.id).first()
    if not row:
        return RatingResponse(session_id=session_id, rated=False)
    return RatingResponse(session_id=session_id, stars=row.stars, comment=row.comment,
                          speaker_feedback=row.speaker_feedback, rated=True,
                          updated_at=row.updated_at)


@router.get("/sessions/{session_id}/rating/summary", response_model=SessionSummary)
def session_summary(session_id: int, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """Full summary for organizers; anonymized (no private feedback) for the
    session's own speaker."""
    item = _session_or_404(db, session_id)
    is_org = _is_organizer(user)
    # The session's speaker and its chair both get the anonymized summary.
    is_owner = item.speaker_id == user.id or item.chair_id == user.id
    if not (is_org or is_owner):
        raise HTTPException(status_code=403, detail="Not allowed")
    return _summary(db, item, include_private=is_org)


@router.get("/feedback/mine", response_model=List[MyRating])
def my_ratings(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (db.query(SessionRating, ScheduleItem.title)
            .join(ScheduleItem, ScheduleItem.id == SessionRating.schedule_item_id)
            .filter(SessionRating.user_id == user.id)
            .order_by(SessionRating.updated_at.desc()).all())
    return [MyRating(session_id=r.schedule_item_id, title=t, stars=r.stars,
                     comment=r.comment, updated_at=r.updated_at) for r, t in rows]


# ─── Post-event survey ───────────────────────────────────────────
@router.get("/feedback/survey", response_model=SurveyResponseOut)
def get_survey(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.query(SurveyResponse).filter(SurveyResponse.user_id == user.id).first()
    if not row:
        return SurveyResponseOut(submitted=False)
    return SurveyResponseOut(
        submitted=True, overall=row.overall, organization=row.organization,
        venue=row.venue, would_recommend=row.would_recommend, comment=row.comment,
        created_at=row.created_at)


@router.post("/feedback/survey", response_model=SurveyResponseOut, status_code=201)
def submit_survey(req: SurveyCreate, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    row = db.query(SurveyResponse).filter(SurveyResponse.user_id == user.id).first()
    if row:                       # idempotent — update the existing response
        row.overall = req.overall
        row.organization = req.organization
        row.venue = req.venue
        row.would_recommend = req.would_recommend
        row.comment = req.comment
    else:
        row = SurveyResponse(
            user_id=user.id, overall=req.overall, organization=req.organization,
            venue=req.venue, would_recommend=req.would_recommend, comment=req.comment)
        db.add(row)
    db.commit()
    db.refresh(row)
    return SurveyResponseOut(
        submitted=True, overall=row.overall, organization=row.organization,
        venue=row.venue, would_recommend=row.would_recommend, comment=row.comment,
        created_at=row.created_at)


def _survey_aggregate(db: Session) -> SurveyAggregate:
    rows = db.query(SurveyResponse).all()
    n = len(rows)
    rec = [r for r in rows if r.would_recommend is not None]
    return SurveyAggregate(
        responses=n,
        avg_overall=_avg([r.overall for r in rows]),
        avg_organization=_avg([r.organization for r in rows]),
        avg_venue=_avg([r.venue for r in rows]),
        recommend_pct=round(100 * sum(1 for r in rec if r.would_recommend) / len(rec), 1) if rec else 0.0,
        comments=[r.comment for r in rows if r.comment][:50],
    )


# ─── Organizer: real-time sentiment dashboard ────────────────────
@router.get("/feedback/sentiment", response_model=SentimentDashboard)
def sentiment(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not _is_organizer(user):
        raise HTTPException(status_code=403, detail="Organizers only")
    rows = db.query(SessionRating).all()
    all_stars = [r.stars for r in rows]

    # Per-session aggregates.
    by_session: Dict[int, list] = {}
    for r in rows:
        by_session.setdefault(r.schedule_item_id, []).append(r)
    titles = dict(db.query(ScheduleItem.id, ScheduleItem.title).filter(
        ScheduleItem.id.in_(list(by_session) or [0])).all())
    summaries = []
    for sid, rs in by_session.items():
        st = [r.stars for r in rs]
        summaries.append(SessionSummary(
            session_id=sid, title=titles.get(sid, f"Session {sid}"),
            average=_avg(st), count=len(rs), distribution=_distribution(rs),
            comments=[r.comment for r in rs if r.comment],
            speaker_feedback=[r.speaker_feedback for r in rs if r.speaker_feedback]))
    ranked = sorted(summaries, key=lambda s: (s.average, s.count), reverse=True)
    low = sorted(summaries, key=lambda s: (s.average, -s.count))

    recent = [r.comment for r in sorted(rows, key=lambda r: r.updated_at, reverse=True)
              if r.comment][:15]

    return SentimentDashboard(
        total_ratings=len(rows), overall_average=_avg(all_stars),
        distribution=_distribution(rows), rated_sessions=len(by_session),
        top_sessions=ranked[:5], low_sessions=low[:5], recent_comments=recent,
        survey=_survey_aggregate(db),
        server_time=datetime.now(timezone.utc).isoformat(),
    )


def _csv_safe(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s and s[0] in ("=", "+", "-", "@", "\t", "\r") else s


@router.get("/feedback/survey/export")
def export_survey(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not _is_organizer(user):
        raise HTTPException(status_code=403, detail="Organizers only")
    rows = db.query(SurveyResponse).order_by(SurveyResponse.created_at.desc()).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Overall", "Organization", "Venue", "Would recommend", "Comment",
                "Submitted (UTC)"])
    for r in rows:
        w.writerow([r.overall, r.organization or "", r.venue or "",
                    "" if r.would_recommend is None else ("yes" if r.would_recommend else "no"),
                    _csv_safe(r.comment or ""),
                    r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""])
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="survey-responses.csv"'})


# ─── Speaker: anonymized digest of my own sessions ───────────────
@router.get("/feedback/my-talks", response_model=SpeakerDigest)
def my_talks_digest(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    items = db.query(ScheduleItem).filter(ScheduleItem.speaker_id == user.id).all()
    summaries = [_summary(db, it, include_private=False) for it in items]
    stars = [r.stars for r in db.query(SessionRating.stars).filter(
        SessionRating.schedule_item_id.in_([it.id for it in items] or [0])).all()]
    return SpeakerDigest(sessions=summaries, overall_average=_avg(stars),
                         total_ratings=len(stars))
