"""
Session Recordings API.

Three gates decide whether an attendee can watch a talk:

1. **Speaker consent** — the presenting speaker grants or denies recording
   consent (``POST /{id}/consent``). Nothing publishes without a grant.
2. **Organizer publication** — an organizer publishes the recording, which
   stamps the availability window (``RECORDING_RETENTION_DAYS``, default 30
   days post-publication).
3. **The window itself** — once ``available_until`` passes, the recording
   drops out of the attendee catalogue (organizers still see it and can
   extend the window).

On top of that: a **searchable transcript** (segments carry timestamps so a
hit can seek the player), **slide-sync markers** ("slide 4 starts at 12:30"),
and a speaker-facing view of their own recordings and consent state.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit_service import log_action
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.core.transcripts import parse_vtt, snippet
from app.models.audit_log import AuditAction
from app.models.notification import UserNotification
from app.models.recording import (
    SessionRecording, SlideMarker, TranscriptSegment,
)
from app.models.schedule import ScheduleItem
from app.models.user import User, UserRole
from app.schemas.recording import (
    ConsentRequest, RecordingCreate, RecordingOut, RecordingUpdate,
    SearchHit, SearchResponse, SegmentOut, SlideOut, SlidesUpload,
    TranscriptUpload,
)

router = APIRouter()

ORGANIZER_ROLES = {UserRole.admin, UserRole.super_admin}
MAX_SEGMENTS = 5000
MAX_SLIDES = 500


def _is_organizer(user: User) -> bool:
    return user.role in ORGANIZER_ROLES


def _require_organizer(user: User) -> None:
    if not _is_organizer(user):
        raise HTTPException(status_code=403, detail="Organizers only")


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def is_available(rec: SessionRecording, now: Optional[datetime] = None) -> bool:
    """Attendee visibility: consented, published, and inside the window."""
    now = now or datetime.now(timezone.utc)
    if rec.consent_state != "granted" or rec.status != "published":
        return False
    until = _as_utc(rec.available_until)
    return until is None or now <= until


def _is_speaker_of(item: Optional[ScheduleItem], user: User) -> bool:
    """The session's presenting speaker — the only person who can consent."""
    return bool(item and item.speaker_id and item.speaker_id == user.id)


def _counts(db: Session, ids: List[int]) -> Dict[int, Dict[str, int]]:
    out: Dict[int, Dict[str, int]] = {i: {"segments": 0, "slides": 0} for i in ids}
    if not ids:
        return out
    for rid, in db.query(TranscriptSegment.recording_id).filter(
            TranscriptSegment.recording_id.in_(ids)).all():
        out[rid]["segments"] += 1
    for rid, in db.query(SlideMarker.recording_id).filter(
            SlideMarker.recording_id.in_(ids)).all():
        out[rid]["slides"] += 1
    return out


def _serialize(rec: SessionRecording, item: Optional[ScheduleItem], user: User,
               *, speaker_name: Optional[str] = None,
               counts: Optional[Dict[str, int]] = None,
               transcript: Optional[List[TranscriptSegment]] = None,
               slides: Optional[List[SlideMarker]] = None) -> RecordingOut:
    now = datetime.now(timezone.utc)
    until = _as_utc(rec.available_until)
    days_left = None
    if until:
        days_left = max(0, (until - now).days)
    counts = counts or {"segments": 0, "slides": 0}
    return RecordingOut(
        id=rec.id,
        schedule_item_id=rec.schedule_item_id,
        session_title=item.title if item else "(deleted session)",
        session_start=item.start_time if item else None,
        speaker_name=speaker_name,
        video_url=rec.video_url,
        slides_url=rec.slides_url,
        duration_seconds=rec.duration_seconds,
        status=rec.status,
        consent_state=rec.consent_state,
        consent_note=rec.consent_note,
        published_at=rec.published_at,
        available_until=rec.available_until,
        days_left=days_left,
        expired=bool(until and now > until),
        available=is_available(rec, now),
        views=rec.views or 0,
        segment_count=counts["segments"],
        slide_count=counts["slides"],
        can_manage=_is_organizer(user),
        can_consent=_is_speaker_of(item, user) or _is_organizer(user),
        transcript=[SegmentOut(id=s.id, start_seconds=s.start_seconds,
                               end_seconds=s.end_seconds,
                               speaker_label=s.speaker_label, text=s.text)
                    for s in (transcript or [])],
        slides=[SlideOut(id=s.id, at_seconds=s.at_seconds,
                         slide_number=s.slide_number, title=s.title,
                         image_url=s.image_url)
                for s in (slides or [])],
    )


def _load(db: Session, recording_id: int):
    rec = db.query(SessionRecording).filter(
        SessionRecording.id == recording_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found")
    item = db.query(ScheduleItem).filter(
        ScheduleItem.id == rec.schedule_item_id).first()
    return rec, item


def _speaker_names(db: Session, items: List[ScheduleItem]) -> Dict[int, str]:
    """Session id → presenter name (the linked account, else extra.speaker)."""
    ids = {it.speaker_id for it in items if it.speaker_id}
    accounts = {}
    if ids:
        accounts = {u.id: u.full_name for u in
                    db.query(User).filter(User.id.in_(ids)).all()}
    out = {}
    for it in items:
        name = accounts.get(it.speaker_id) if it.speaker_id else None
        if not name:
            name = (it.extra or {}).get("speaker")
        if name:
            out[it.id] = name
    return out


# ─── Catalogue ───────────────────────────────────────────────────
@router.get("", response_model=List[RecordingOut])
@router.get("/", response_model=List[RecordingOut])
def list_recordings(mine: bool = Query(False, description="Only my own talks"),
                    include_hidden: bool = Query(
                        False, description="Organizers: include unpublished/expired"),
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    recs = db.query(SessionRecording).order_by(
        SessionRecording.created_at.desc()).all()
    if not recs:
        return []
    items = {it.id: it for it in db.query(ScheduleItem).filter(
        ScheduleItem.id.in_([r.schedule_item_id for r in recs])).all()}
    names = _speaker_names(db, list(items.values()))
    counts = _counts(db, [r.id for r in recs])
    now = datetime.now(timezone.utc)

    out = []
    for rec in recs:
        item = items.get(rec.schedule_item_id)
        own = _is_speaker_of(item, user)
        if mine and not own:
            continue
        # Attendees only ever see live recordings; speakers always see their
        # own (that's where they act on consent); organizers see everything
        # when they ask for it.
        if not is_available(rec, now) and not own:
            if not (_is_organizer(user) and include_hidden):
                continue
        out.append(_serialize(rec, item, user,
                              speaker_name=names.get(rec.schedule_item_id),
                              counts=counts.get(rec.id)))
    return out


@router.get("/search", response_model=SearchResponse)
def search_transcripts(q: str = Query(..., min_length=2),
                       limit: int = Query(40, ge=1, le=200),
                       db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """Full-text search across every transcript the caller may watch."""
    term = q.strip()
    # Escape LIKE wildcards so a query of "%_" isn't treated as a pattern.
    like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    # Bound the DB fetch: we post-filter by availability, so over-fetch a
    # multiple of `limit` rather than pulling every matching segment into RAM.
    rows = (db.query(TranscriptSegment, SessionRecording, ScheduleItem)
            .join(SessionRecording,
                  SessionRecording.id == TranscriptSegment.recording_id)
            .outerjoin(ScheduleItem,
                       ScheduleItem.id == SessionRecording.schedule_item_id)
            .filter(TranscriptSegment.text.ilike(like, escape="\\"))
            .order_by(TranscriptSegment.recording_id,
                      TranscriptSegment.start_seconds)
            .limit(limit * 20)
            .all())
    now = datetime.now(timezone.utc)
    hits = []
    for seg, rec, item in rows:
        if not is_available(rec, now) and not (
                _is_organizer(user) or _is_speaker_of(item, user)):
            continue
        hits.append(SearchHit(
            recording_id=rec.id, schedule_item_id=rec.schedule_item_id,
            session_title=item.title if item else "(deleted session)",
            start_seconds=seg.start_seconds, speaker_label=seg.speaker_label,
            text=snippet(seg.text, term)))
    return SearchResponse(query=term, total=len(hits), hits=hits[:limit])


@router.get("/{recording_id}", response_model=RecordingOut)
def get_recording(recording_id: int, q: Optional[str] = Query(None),
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """Recording detail with its transcript and slide markers.

    ``q`` filters the transcript to matching segments (each keeping its
    timestamp, so the player can jump straight there)."""
    rec, item = _load(db, recording_id)
    if not is_available(rec) and not (_is_organizer(user)
                                      or _is_speaker_of(item, user)):
        raise HTTPException(status_code=404, detail="Recording not available")

    seg_q = db.query(TranscriptSegment).filter(
        TranscriptSegment.recording_id == rec.id)
    if q and q.strip():
        seg_q = seg_q.filter(TranscriptSegment.text.ilike(f"%{q.strip()}%"))
    segments = seg_q.order_by(TranscriptSegment.start_seconds).all()
    slides = (db.query(SlideMarker).filter(SlideMarker.recording_id == rec.id)
              .order_by(SlideMarker.at_seconds).all())
    names = _speaker_names(db, [item] if item else [])
    counts = _counts(db, [rec.id])
    return _serialize(rec, item, user, speaker_name=names.get(rec.schedule_item_id),
                      counts=counts.get(rec.id), transcript=segments, slides=slides)


@router.post("/{recording_id}/view", response_model=RecordingOut)
def register_view(recording_id: int, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """Count a playback. Organizer/speaker previews don't inflate the number."""
    rec, item = _load(db, recording_id)
    if not is_available(rec):
        raise HTTPException(status_code=404, detail="Recording not available")
    if not (_is_organizer(user) or _is_speaker_of(item, user)):
        # Atomic increment in SQL so concurrent playbacks don't lose counts to
        # a read-modify-write race.
        db.query(SessionRecording).filter(SessionRecording.id == rec.id).update(
            {SessionRecording.views: func.coalesce(SessionRecording.views, 0) + 1},
            synchronize_session=False)
        db.commit()
        db.refresh(rec)
    counts = _counts(db, [rec.id])
    return _serialize(rec, item, user, counts=counts.get(rec.id))


# ─── Speaker consent (REC-01) ────────────────────────────────────
@router.post("/{recording_id}/consent", response_model=RecordingOut)
def set_consent(recording_id: int, req: ConsentRequest, request: Request,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    """Grant or deny recording consent.

    Only the session's presenting speaker decides. An organizer may record the
    decision on their behalf (signed release) — every write is audit-logged
    with who actually clicked it.
    """
    rec, item = _load(db, recording_id)
    if not (_is_speaker_of(item, user) or _is_organizer(user)):
        raise HTTPException(status_code=403,
                            detail="Only the session's speaker can decide this")
    old = rec.consent_state
    rec.consent_state = req.decision
    rec.consent_by = user.id
    rec.consent_at = datetime.now(timezone.utc)
    rec.consent_note = req.note
    # Denying consent immediately pulls the recording from the catalogue.
    if req.decision == "denied" and rec.status == "published":
        rec.status = "withheld"
    db.commit()
    log_action(db, user, AuditAction.settings_change,
               f"Recording consent {req.decision} for '"
               f"{item.title if item else rec.schedule_item_id}'",
               request=request, target_type="recording", target_id=rec.id,
               old_value=old, new_value=req.decision)
    counts = _counts(db, [rec.id])
    return _serialize(rec, item, user, counts=counts.get(rec.id))


# ─── Organizer: create / update / publish ────────────────────────
@router.post("", response_model=RecordingOut, status_code=201)
@router.post("/", response_model=RecordingOut, status_code=201)
def create_recording(req: RecordingCreate, request: Request,
                     db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    _require_organizer(user)
    item = db.query(ScheduleItem).filter(
        ScheduleItem.id == req.schedule_item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Session not found")
    if db.query(SessionRecording).filter(
            SessionRecording.schedule_item_id == item.id).first():
        raise HTTPException(status_code=400,
                            detail="This session already has a recording")
    rec = SessionRecording(
        schedule_item_id=item.id, video_url=req.video_url,
        slides_url=req.slides_url, duration_seconds=req.duration_seconds,
        created_by=user.id)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    log_action(db, user, AuditAction.settings_change,
               f"Created recording for '{item.title}'", request=request,
               target_type="recording", target_id=rec.id)
    # Ask the presenting speaker for consent right away.
    if item.speaker_id:
        db.add(UserNotification(
            user_id=item.speaker_id, schedule_item_id=item.id,
            kind="recording_consent",
            title="Recording consent needed",
            body=f"Your session '{item.title}' was recorded. Open Recordings "
                 f"to grant or deny consent to publish it to attendees."))
        db.commit()
    return _serialize(rec, item, user)


@router.put("/{recording_id}", response_model=RecordingOut)
def update_recording(recording_id: int, req: RecordingUpdate, request: Request,
                     db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    _require_organizer(user)
    rec, item = _load(db, recording_id)
    for field, value in req.model_dump(exclude_unset=True).items():
        setattr(rec, field, value)
    rec.updated_at = datetime.now(timezone.utc)
    db.commit()
    log_action(db, user, AuditAction.settings_change,
               f"Updated recording #{rec.id}", request=request,
               target_type="recording", target_id=rec.id)
    counts = _counts(db, [rec.id])
    return _serialize(rec, item, user, counts=counts.get(rec.id))


@router.post("/{recording_id}/publish", response_model=RecordingOut)
def publish_recording(recording_id: int, request: Request,
                      days: Optional[int] = Query(
                          None, ge=1, le=3650,
                          description="Availability window; default "
                                      "RECORDING_RETENTION_DAYS"),
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Publish to attendees. Refuses without the speaker's consent (REC-01)."""
    _require_organizer(user)
    rec, item = _load(db, recording_id)
    if rec.consent_state != "granted":
        raise HTTPException(
            status_code=400,
            detail="The speaker has not granted recording consent")
    if not rec.video_url:
        raise HTTPException(status_code=400, detail="Add a video link first")
    now = datetime.now(timezone.utc)
    window = days or get_settings().RECORDING_RETENTION_DAYS
    rec.status = "published"
    rec.published_at = now
    rec.available_until = now + timedelta(days=window)
    db.commit()
    log_action(db, user, AuditAction.content_approve,
               f"Published recording for '{item.title if item else rec.id}' "
               f"({window}-day window)", request=request,
               target_type="recording", target_id=rec.id)
    counts = _counts(db, [rec.id])
    return _serialize(rec, item, user, counts=counts.get(rec.id))


@router.post("/{recording_id}/withhold", response_model=RecordingOut)
def withhold_recording(recording_id: int, request: Request,
                       db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """Pull a published recording back out of the catalogue."""
    _require_organizer(user)
    rec, item = _load(db, recording_id)
    rec.status = "withheld"
    db.commit()
    log_action(db, user, AuditAction.content_remove,
               f"Withheld recording for '{item.title if item else rec.id}'",
               request=request, target_type="recording", target_id=rec.id)
    counts = _counts(db, [rec.id])
    return _serialize(rec, item, user, counts=counts.get(rec.id))


@router.delete("/{recording_id}", status_code=204)
def delete_recording(recording_id: int, request: Request,
                     db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    _require_organizer(user)
    rec, item = _load(db, recording_id)
    # Explicit child cleanup — SQLite doesn't enforce ON DELETE CASCADE.
    db.query(TranscriptSegment).filter(
        TranscriptSegment.recording_id == rec.id).delete(synchronize_session=False)
    db.query(SlideMarker).filter(
        SlideMarker.recording_id == rec.id).delete(synchronize_session=False)
    db.delete(rec)
    db.commit()
    log_action(db, user, AuditAction.content_remove,
               f"Deleted recording for '{item.title if item else recording_id}'",
               request=request, target_type="recording", target_id=recording_id)
    return None


# ─── Organizer: transcript + slide markers ───────────────────────
@router.post("/{recording_id}/transcript", response_model=RecordingOut)
def upload_transcript(recording_id: int, req: TranscriptUpload, request: Request,
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Replace the transcript, either as structured segments or a VTT/SRT blob."""
    _require_organizer(user)
    rec, item = _load(db, recording_id)

    if req.segments:
        rows = [s.model_dump() for s in req.segments]
    elif req.vtt:
        rows = parse_vtt(req.vtt)
        if not rows:
            raise HTTPException(status_code=400,
                                detail="No cues found in that transcript")
    else:
        raise HTTPException(status_code=400,
                            detail="Provide either 'segments' or 'vtt'")
    if len(rows) > MAX_SEGMENTS:
        raise HTTPException(status_code=400,
                            detail=f"Transcript is too long (max {MAX_SEGMENTS} segments)")

    db.query(TranscriptSegment).filter(
        TranscriptSegment.recording_id == rec.id).delete(synchronize_session=False)
    for row in rows:
        db.add(TranscriptSegment(recording_id=rec.id, **row))
    rec.updated_at = datetime.now(timezone.utc)
    db.commit()
    log_action(db, user, AuditAction.settings_change,
               f"Uploaded transcript ({len(rows)} segments) for recording #{rec.id}",
               request=request, target_type="recording", target_id=rec.id)
    segments = (db.query(TranscriptSegment)
                .filter(TranscriptSegment.recording_id == rec.id)
                .order_by(TranscriptSegment.start_seconds).all())
    slides = (db.query(SlideMarker).filter(SlideMarker.recording_id == rec.id)
              .order_by(SlideMarker.at_seconds).all())
    counts = _counts(db, [rec.id])
    return _serialize(rec, item, user, counts=counts.get(rec.id),
                      transcript=segments, slides=slides)


@router.post("/{recording_id}/slides", response_model=RecordingOut)
def upload_slides(recording_id: int, req: SlidesUpload, request: Request,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """Replace the slide-sync markers ("slide N starts at t")."""
    _require_organizer(user)
    rec, item = _load(db, recording_id)
    if len(req.slides) > MAX_SLIDES:
        raise HTTPException(status_code=400,
                            detail=f"Too many slides (max {MAX_SLIDES})")
    seen = set()
    rows = []
    for slide in req.slides:
        if slide.slide_number in seen:
            raise HTTPException(
                status_code=400,
                detail=f"Duplicate slide number {slide.slide_number}")
        seen.add(slide.slide_number)
        rows.append(slide.model_dump())

    db.query(SlideMarker).filter(
        SlideMarker.recording_id == rec.id).delete(synchronize_session=False)
    for row in rows:
        db.add(SlideMarker(recording_id=rec.id, **row))
    rec.updated_at = datetime.now(timezone.utc)
    db.commit()
    log_action(db, user, AuditAction.settings_change,
               f"Set {len(rows)} slide markers on recording #{rec.id}",
               request=request, target_type="recording", target_id=rec.id)
    segments = (db.query(TranscriptSegment)
                .filter(TranscriptSegment.recording_id == rec.id)
                .order_by(TranscriptSegment.start_seconds).all())
    slides = (db.query(SlideMarker).filter(SlideMarker.recording_id == rec.id)
              .order_by(SlideMarker.at_seconds).all())
    counts = _counts(db, [rec.id])
    return _serialize(rec, item, user, counts=counts.get(rec.id),
                      transcript=segments, slides=slides)
