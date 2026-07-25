"""
Sponsor & Exhibitor Portal API.

- **Attendees** browse the tiered sponsor directory, open a virtual booth
  (about + promo video + brochure + team bios), and — with **explicit opt-in
  consent** (DATA-06) — submit a lead so the sponsor can follow up.
- **Organizers** (admin / super_admin) create and manage sponsors, upload
  logos, read/export leads (PII — logged per DATA-03), and see per-sponsor
  analytics (booth visits, unique visitors, leads, conversion).

Sponsor logos surface across the app (home rail, presenter/venue screens) via
the public ``GET /api/sponsors`` list.
"""
import csv
import io
import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import (APIRouter, Depends, HTTPException, Query, Request,
                     UploadFile, File)
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.uploads import read_capped
from app.core.database import get_db
from app.core.security import get_current_user
from app.core import sponsor_files
from app.core.audit_service import log_action
from app.models.user import User, UserRole
from app.models.audit_log import AuditAction
from app.models.sponsor import Sponsor, SponsorLead, SponsorVisit, TIER_RANK
from app.schemas.sponsor import (
    SponsorCreate, SponsorUpdate, SponsorResponse, SponsorListResponse,
    TeamMember, LeadCreate, LeadResponse, SponsorAnalytics, AnalyticsResponse,
)

router = APIRouter()

ORGANIZER_ROLES = {UserRole.admin, UserRole.super_admin}


def _is_organizer(user: User) -> bool:
    return user.role in ORGANIZER_ROLES


def _require_organizer(user: User) -> None:
    if not _is_organizer(user):
        raise HTTPException(status_code=403, detail="Organizers only")


def _get(db: Session, sponsor_id: int) -> Sponsor:
    s = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Sponsor not found")
    return s


def _team_out(raw) -> List[TeamMember]:
    if not raw:
        return []
    out = []
    for m in raw:
        if isinstance(m, dict) and m.get("name"):
            out.append(TeamMember(name=m["name"], title=m.get("title"),
                                  bio=m.get("bio")))
    return out


def _serialize(db: Session, s: Sponsor, viewer: User, *, detail: bool = False) -> SponsorResponse:
    can_manage = _is_organizer(viewer)
    lead_submitted = db.query(SponsorLead.id).filter(
        SponsorLead.sponsor_id == s.id, SponsorLead.user_id == viewer.id).first() is not None

    resp = SponsorResponse(
        id=s.id, name=s.name, tier=s.tier, tagline=s.tagline,
        website_url=s.website_url, has_logo=bool(s.stored_logo),
        booth_number=s.booth_number, is_active=s.is_active,
        display_order=s.display_order, lead_submitted=lead_submitted,
        can_manage=can_manage, created_at=s.created_at, updated_at=s.updated_at,
    )
    if detail:
        resp.about = s.about
        resp.video_url = s.video_url
        resp.brochure_url = s.brochure_url
        resp.team = _team_out(s.team)
    if can_manage:
        resp.unique_visitors = db.query(func.count(SponsorVisit.id)).filter(
            SponsorVisit.sponsor_id == s.id).scalar() or 0
        resp.total_visits = db.query(func.coalesce(func.sum(SponsorVisit.visits), 0)).filter(
            SponsorVisit.sponsor_id == s.id).scalar() or 0
        resp.lead_count = db.query(func.count(SponsorLead.id)).filter(
            SponsorLead.sponsor_id == s.id).scalar() or 0
    return resp


def _sorted(sponsors: List[Sponsor]) -> List[Sponsor]:
    return sorted(sponsors, key=lambda s: (TIER_RANK.get(s.tier, 99),
                                           s.display_order, s.name.lower()))


# ─── Directory ───────────────────────────────────────────────────
@router.get("", response_model=SponsorListResponse)
@router.get("/", response_model=SponsorListResponse)
def list_sponsors(include_inactive: bool = Query(False),
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """Tiered sponsor directory. Attendees see active sponsors; organizers can
    include hidden ones with ``include_inactive``."""
    q = db.query(Sponsor)
    if not (include_inactive and _is_organizer(user)):
        q = q.filter(Sponsor.is_active == True)  # noqa: E712
    sponsors = _sorted(q.all())
    return SponsorListResponse(
        sponsors=[_serialize(db, s, user) for s in sponsors],
        total=len(sponsors), can_manage=_is_organizer(user))


@router.post("", response_model=SponsorResponse, status_code=201)
@router.post("/", response_model=SponsorResponse, status_code=201)
def create_sponsor(req: SponsorCreate, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    _require_organizer(user)
    s = Sponsor(
        name=req.name, tier=req.tier, tagline=req.tagline,
        website_url=req.website_url, about=req.about, video_url=req.video_url,
        brochure_url=req.brochure_url, booth_number=req.booth_number,
        team=[m.model_dump() for m in req.team] if req.team else None,
        is_active=req.is_active, display_order=req.display_order,
        created_by=user.id,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    log_action(db, user, AuditAction.sponsor_create,
               f"Created sponsor '{s.name}' ({s.tier})",
               request=request, target_type="sponsor", target_id=s.id)
    return _serialize(db, s, user, detail=True)


# ─── Analytics (organizer) — before /{sponsor_id} ────────────────
@router.get("/analytics", response_model=AnalyticsResponse)
def sponsor_analytics(db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Per-sponsor engagement: unique visitors, total visits, leads, and the
    lead conversion rate (leads / unique visitors)."""
    _require_organizer(user)
    sponsors = _sorted(db.query(Sponsor).all())
    visit_rows = dict(db.query(
        SponsorVisit.sponsor_id, func.count(SponsorVisit.id)).group_by(
        SponsorVisit.sponsor_id).all())
    total_visit_rows = dict(db.query(
        SponsorVisit.sponsor_id, func.coalesce(func.sum(SponsorVisit.visits), 0)).group_by(
        SponsorVisit.sponsor_id).all())
    lead_rows = dict(db.query(
        SponsorLead.sponsor_id, func.count(SponsorLead.id)).group_by(
        SponsorLead.sponsor_id).all())

    out = []
    for s in sponsors:
        uniq = int(visit_rows.get(s.id, 0))
        total = int(total_visit_rows.get(s.id, 0))
        leads = int(lead_rows.get(s.id, 0))
        out.append(SponsorAnalytics(
            sponsor_id=s.id, name=s.name, tier=s.tier,
            unique_visitors=uniq, total_visits=total, leads=leads,
            conversion_rate=round(leads / uniq, 3) if uniq else 0.0))
    totals = {
        "sponsors": len(sponsors),
        "unique_visitors": sum(a.unique_visitors for a in out),
        "total_visits": sum(a.total_visits for a in out),
        "leads": sum(a.leads for a in out),
    }
    return AnalyticsResponse(sponsors=out, totals=totals)


# ─── Single sponsor / booth ──────────────────────────────────────
@router.get("/{sponsor_id}", response_model=SponsorResponse)
def get_sponsor(sponsor_id: int, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    """Booth detail. Opening a booth as an attendee records a visit (one row
    per attendee with a repeat counter, for analytics)."""
    s = _get(db, sponsor_id)
    if not s.is_active and not _is_organizer(user):
        raise HTTPException(status_code=404, detail="Sponsor not found")
    if not _is_organizer(user):
        _record_visit(db, sponsor_id, user.id)
    return _serialize(db, s, user, detail=True)


def _record_visit(db: Session, sponsor_id: int, user_id: int) -> None:
    row = db.query(SponsorVisit).filter(
        SponsorVisit.sponsor_id == sponsor_id,
        SponsorVisit.user_id == user_id).first()
    if row:
        row.visits += 1
        row.last_visit_at = datetime.now(timezone.utc)
        db.commit()
        return
    db.add(SponsorVisit(sponsor_id=sponsor_id, user_id=user_id, visits=1))
    try:
        db.commit()
    except IntegrityError:      # concurrent first visit — fold into the winner
        db.rollback()
        row = db.query(SponsorVisit).filter(
            SponsorVisit.sponsor_id == sponsor_id,
            SponsorVisit.user_id == user_id).first()
        if row:
            row.visits += 1
            row.last_visit_at = datetime.now(timezone.utc)
            db.commit()


@router.put("/{sponsor_id}", response_model=SponsorResponse)
def update_sponsor(sponsor_id: int, req: SponsorUpdate, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    _require_organizer(user)
    s = _get(db, sponsor_id)
    for f in ("name", "tier", "tagline", "website_url", "about", "video_url",
              "brochure_url", "booth_number", "is_active", "display_order"):
        val = getattr(req, f)
        if val is not None:
            setattr(s, f, val.strip() if isinstance(val, str) else val)
    if req.team is not None:
        s.team = [m.model_dump() for m in req.team]
    s.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(s)
    log_action(db, user, AuditAction.sponsor_update,
               f"Updated sponsor '{s.name}'",
               request=request, target_type="sponsor", target_id=s.id)
    return _serialize(db, s, user, detail=True)


@router.delete("/{sponsor_id}", status_code=204)
def delete_sponsor(sponsor_id: int, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    _require_organizer(user)
    s = _get(db, sponsor_id)
    stored, name = s.stored_logo, s.name
    # Explicitly remove children (FK ON DELETE CASCADE isn't enforced on every
    # backend, e.g. SQLite without the pragma) so leads/visits never orphan.
    db.query(SponsorLead).filter(SponsorLead.sponsor_id == sponsor_id).delete()
    db.query(SponsorVisit).filter(SponsorVisit.sponsor_id == sponsor_id).delete()
    db.delete(s)
    db.commit()
    if stored:
        sponsor_files.delete_file(stored)
    log_action(db, user, AuditAction.sponsor_delete,
               f"Deleted sponsor '{name}'",
               request=request, target_type="sponsor", target_id=sponsor_id)
    return None


# ─── Lead capture (attendee, opt-in per DATA-06) ─────────────────
@router.post("/{sponsor_id}/lead", response_model=SponsorResponse, status_code=201)
def submit_lead(sponsor_id: int, req: LeadCreate, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    s = _get(db, sponsor_id)
    if not s.is_active:
        raise HTTPException(status_code=404, detail="Sponsor not found")
    if not req.consent:
        raise HTTPException(
            status_code=400,
            detail="Consent is required to share your details with the sponsor")
    existing = db.query(SponsorLead).filter(
        SponsorLead.sponsor_id == sponsor_id,
        SponsorLead.user_id == user.id).first()
    if existing:
        # Idempotent: let them update the note without creating a duplicate.
        existing.message = req.message
        existing.consent = True
        db.commit()
        return _serialize(db, s, user, detail=True)
    db.add(SponsorLead(sponsor_id=sponsor_id, user_id=user.id,
                       consent=True, message=req.message))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return _serialize(db, s, user, detail=True)


# ─── Leads (organizer, PII — logged per DATA-03) ─────────────────
def _lead_rows(db: Session, sponsor_id: int):
    return (db.query(SponsorLead, User)
            .join(User, User.id == SponsorLead.user_id)
            .filter(SponsorLead.sponsor_id == sponsor_id)
            .order_by(SponsorLead.created_at.desc()).all())


@router.get("/{sponsor_id}/leads", response_model=List[LeadResponse])
def list_leads(sponsor_id: int, request: Request, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    _require_organizer(user)
    s = _get(db, sponsor_id)
    rows = _lead_rows(db, sponsor_id)
    log_action(db, user, AuditAction.export_data,
               f"Viewed {len(rows)} lead(s) for sponsor '{s.name}'",
               request=request, target_type="sponsor", target_id=sponsor_id)
    return [LeadResponse(
        id=lead.id, sponsor_id=lead.sponsor_id, user_id=lead.user_id,
        name=u.full_name, email=u.email, institution=u.institution,
        message=lead.message, created_at=lead.created_at) for lead, u in rows]


def _csv_safe(value) -> str:
    s = "" if value is None else str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


@router.get("/{sponsor_id}/leads/export")
def export_leads(sponsor_id: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    _require_organizer(user)
    s = _get(db, sponsor_id)
    rows = _lead_rows(db, sponsor_id)
    buf = io.StringIO()
    w = csv.writer(buf)

    def row(*cells):
        w.writerow([_csv_safe(c) for c in cells])

    row(f"Leads — {s.name} ({s.tier})")
    row("Generated (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    row()
    row("Name", "Email", "Institution", "Message", "Consented at (UTC)")
    for lead, u in rows:
        row(u.full_name, u.email, u.institution or "", lead.message or "",
            lead.created_at.strftime("%Y-%m-%d %H:%M") if lead.created_at else "")
    row()
    row("Total leads", len(rows))
    log_action(db, user, AuditAction.export_data,
               f"Exported {len(rows)} lead(s) for sponsor '{s.name}'",
               request=request, target_type="sponsor", target_id=sponsor_id)
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="leads-sponsor-{sponsor_id}.csv"'})


# ─── Logo (upload / serve) ───────────────────────────────────────
@router.post("/{sponsor_id}/logo", response_model=SponsorResponse)
async def upload_logo(sponsor_id: int, request: Request, file: UploadFile = File(...),
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    _require_organizer(user)
    s = _get(db, sponsor_id)
    if not sponsor_files.is_allowed(file.filename or ""):
        raise HTTPException(status_code=422,
                            detail=f"Only {sponsor_files.EXT_LABEL} files are allowed")
    data = await read_capped(file, sponsor_files.max_bytes(), request)
    if not data:
        raise HTTPException(status_code=422, detail="The file is empty")
    old = s.stored_logo
    s.stored_logo = sponsor_files.save_bytes(data, file.filename)
    s.logo_name = (file.filename or "logo")[:255]
    s.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(s)
    if old:
        sponsor_files.delete_file(old)
    return _serialize(db, s, user, detail=True)


@router.get("/{sponsor_id}/logo")
def get_logo(sponsor_id: int, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    s = _get(db, sponsor_id)
    if not s.stored_logo:
        raise HTTPException(status_code=404, detail="No logo uploaded")
    path = sponsor_files.path_for(s.stored_logo)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Logo is missing")
    return FileResponse(path, media_type=sponsor_files.content_type(s.stored_logo),
                        filename=s.logo_name or "logo")
