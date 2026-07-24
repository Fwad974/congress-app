from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user_optional
from app.core.oauth import enabled_providers
from app.models.user import UserRole

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
# Always defined so templates can call it even before lifespan overrides it
# with the real, DB-backed accessor.
templates.env.globals.setdefault("feature_enabled", lambda key: True)


def _feature_blocked(user, key: str) -> bool:
    """A released feature is visible to all; when hidden, only admins may preview."""
    from app.core.feature_flags import is_enabled
    return not is_enabled(key) and user.role not in (UserRole.admin, UserRole.super_admin)


@router.get("/logout")
async def logout_page():
    resp = RedirectResponse(url="/", status_code=302)
    for p in ["/", "/api", "/api/auth", "/home", "/profile", "/settings", "/certificates", "/admin", "/notes", "/qa", "/present", "/papers", "/posters", "/connect", "/moderation"]:
        resp.set_cookie(key="access_token", value="deleted", path=p, max_age=0, httponly=True, samesite="lax")
        resp.set_cookie(key="access_token", value="deleted", path=p, max_age=0, httponly=False, samesite="lax")
        resp.set_cookie(key="access_token", value="deleted", path=p, max_age=0)
    return resp


@router.get("/", response_class=HTMLResponse)
async def landing_page(request: Request, user=Depends(get_current_user_optional)):
    if user:
        return RedirectResponse(url="/home", status_code=302)
    return templates.TemplateResponse("landing.html", {"request": request})


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request, user=Depends(get_current_user_optional)):
    if user:
        return RedirectResponse(url="/home", status_code=302)
    return templates.TemplateResponse(
        "signup.html", {"request": request, "oauth_providers": enabled_providers()})


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user=Depends(get_current_user_optional)):
    if user:
        return RedirectResponse(url="/home", status_code=302)
    return templates.TemplateResponse(
        "login.html", {"request": request, "oauth_providers": enabled_providers()})


@router.get("/home", response_class=HTMLResponse)
async def home_page(request: Request, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("home.html", {"request": request, "user": user})


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("profile.html", {"request": request, "user": user})


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("settings.html", {"request": request, "user": user})


@router.get("/certificates", response_class=HTMLResponse)
async def certificates_page(request: Request, user=Depends(get_current_user_optional)):
    if not user:
        # Preserve a QR check-in code across the login redirect so scanning the
        # door QR while logged out still records attendance after sign-in.
        code = request.query_params.get("checkin")
        if code:
            from urllib.parse import quote
            return RedirectResponse(url=f"/login?checkin={quote(code[:12])}",
                                    status_code=302)
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("certificates.html", {"request": request, "user": user})


@router.get("/schedule", response_class=HTMLResponse)
async def schedule_page(request: Request, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    is_admin = user.role in (UserRole.admin, UserRole.super_admin)
    return templates.TemplateResponse(
        "schedule.html",
        {"request": request, "user": user, "is_admin": is_admin,
         "is_speaker": user.role == UserRole.speaker,
         "is_session_chair": user.role == UserRole.session_chair},
    )


@router.get("/notes", response_class=HTMLResponse)
async def notes_page(request: Request, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if _feature_blocked(user, "notes"):
        return RedirectResponse(url="/home", status_code=302)
    return templates.TemplateResponse("notes.html", {"request": request, "user": user})


@router.get("/qa/{session_id}", response_class=HTMLResponse)
async def qa_page(request: Request, session_id: int, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if _feature_blocked(user, "qa"):
        return RedirectResponse(url="/home", status_code=302)
    can_moderate = user.role in (
        UserRole.session_chair, UserRole.review_chair, UserRole.moderator,
        UserRole.admin, UserRole.super_admin,
    )
    return templates.TemplateResponse("qa.html", {
        "request": request, "user": user,
        "session_id": session_id, "can_moderate": can_moderate,
    })


@router.get("/present/{session_id}", response_class=HTMLResponse)
async def present_page(request: Request, session_id: int, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("present.html", {
        "request": request, "user": user, "session_id": session_id,
    })


@router.get("/papers", response_class=HTMLResponse)
async def papers_page(request: Request, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if _feature_blocked(user, "papers"):
        return RedirectResponse(url="/home", status_code=302)
    is_chair = user.role in (UserRole.review_chair, UserRole.admin, UserRole.super_admin)
    is_reviewer = user.role in (UserRole.reviewer, UserRole.review_chair)
    from app.core.config import get_settings
    from app.models.paper import RUBRIC_CRITERIA
    import json as _json
    return templates.TemplateResponse("papers.html", {
        "request": request, "user": user,
        "is_chair": is_chair, "is_reviewer": is_reviewer,
        "max_upload_mb": get_settings().MAX_UPLOAD_MB,
        "rubric_json": _json.dumps(RUBRIC_CRITERIA),
    })


@router.get("/moderation", response_class=HTMLResponse)
async def moderation_page(request: Request, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    from app.core.admin_security import is_moderator
    if not is_moderator(user):
        return RedirectResponse(url="/home", status_code=302)
    return templates.TemplateResponse("moderation.html", {"request": request, "user": user})


@router.get("/connect", response_class=HTMLResponse)
async def connect_page(request: Request, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if _feature_blocked(user, "connect"):
        return RedirectResponse(url="/home", status_code=302)
    return templates.TemplateResponse("connect.html", {"request": request, "user": user})


@router.get("/schedule/{item_id}/qr-print", response_class=HTMLResponse)
async def session_qr_print(request: Request, item_id: int,
                           user=Depends(get_current_user_optional),
                           db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role not in (UserRole.admin, UserRole.super_admin):
        return RedirectResponse(url="/schedule", status_code=302)
    from app.models.schedule import ScheduleItem
    from app.api.certificates import ensure_attend_code
    item = db.query(ScheduleItem).filter(ScheduleItem.id == item_id).first()
    if not item:
        return RedirectResponse(url="/schedule", status_code=302)
    ensure_attend_code(db, item)
    return templates.TemplateResponse("session_qr.html", {
        "request": request, "user": user, "item": item,
    })


@router.get("/certificates/{kind}/view", response_class=HTMLResponse)
async def certificate_view(request: Request, kind: str,
                           user=Depends(get_current_user_optional),
                           db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    from app.api.certificates import (CERT_KINDS, cert_status, ensure_issued,
                                      _public_base)
    if kind not in CERT_KINDS:
        return RedirectResponse(url="/certificates", status_code=302)
    status = cert_status(db, user)
    cert = next((c for c in status.certificates if c.kind == kind), None)
    if not cert or not cert.unlocked:
        return RedirectResponse(url="/certificates", status_code=302)
    issued = ensure_issued(db, user, kind, status)
    if issued.revoked:
        return RedirectResponse(url="/certificates", status_code=302)
    verify_url = f"{_public_base(request)}/verify/{issued.serial}"
    from app.core.qr import qr_svg_document
    return templates.TemplateResponse("certificate_view.html", {
        "request": request, "user": user, "cert": cert,
        "issued_on": issued.issued_at.strftime("%d %B %Y"),
        "serial": issued.serial,
        # Actual earned credits (uncapped) — cert.progress is capped at the goal.
        "credits": status.credits,
        "verify_qr_svg": qr_svg_document(verify_url, scale=3),
        "verify_url": verify_url,
    })


@router.get("/verify/{serial}", response_class=HTMLResponse)
async def certificate_verify_page(request: Request, serial: str,
                                  db: Session = Depends(get_db)):
    """Public certificate verification — no login required, so institutions
    can confirm a serial (or scan the QR on a certificate) directly."""
    from app.models.certificate import IssuedCertificate
    from app.models.user import User as UserModel
    row = (db.query(IssuedCertificate, UserModel)
           .join(UserModel, UserModel.id == IssuedCertificate.user_id)
           .filter(IssuedCertificate.serial == serial.strip().upper()).first())
    titles = {"attendance": "Certificate of Attendance",
              "cme": "CME/CPD Credit Certificate",
              "speaker": "Speaker Certificate"}
    ctx = {"request": request, "serial": serial.strip().upper(), "found": False}
    if row:
        cert, holder = row
        ctx.update({
            "found": True, "revoked": cert.revoked,
            "title": titles.get(cert.kind, cert.kind), "kind": cert.kind,
            "holder": holder.full_name, "institution": holder.institution,
            "issued_on": cert.issued_at.strftime("%d %B %Y"),
            "sessions": cert.sessions_count, "credits": cert.credits,
        })
    return templates.TemplateResponse("certificate_verify.html", ctx)


@router.get("/posters/{poster_id}/qr-print", response_class=HTMLResponse)
async def poster_qr_print(request: Request, poster_id: int,
                          user=Depends(get_current_user_optional),
                          db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    from app.api.posters import _can_edit
    from app.models.poster import Poster
    poster = db.query(Poster).filter(Poster.id == poster_id).first()
    if not poster or not _can_edit(poster, user):
        return RedirectResponse(url="/posters", status_code=302)
    return templates.TemplateResponse("poster_qr.html", {
        "request": request, "user": user, "poster": poster,
    })


@router.get("/feedback", response_class=HTMLResponse)
async def feedback_page(request: Request, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if _feature_blocked(user, "feedback"):
        return RedirectResponse(url="/home", status_code=302)
    is_organizer = user.role in (UserRole.admin, UserRole.super_admin)
    is_speaker = user.role == UserRole.speaker
    return templates.TemplateResponse("feedback.html", {
        "request": request, "user": user,
        "is_organizer": is_organizer, "is_speaker": is_speaker,
    })


@router.get("/sponsors", response_class=HTMLResponse)
async def sponsors_page(request: Request, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if _feature_blocked(user, "sponsors"):
        return RedirectResponse(url="/home", status_code=302)
    from app.models.sponsor import SPONSOR_TIERS
    import json as _json
    is_organizer = user.role in (UserRole.admin, UserRole.super_admin)
    return templates.TemplateResponse("sponsors.html", {
        "request": request, "user": user, "is_organizer": is_organizer,
        "tiers_json": _json.dumps(SPONSOR_TIERS),
    })


@router.get("/posters", response_class=HTMLResponse)
async def posters_page(request: Request, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if _feature_blocked(user, "posters"):
        return RedirectResponse(url="/home", status_code=302)
    from app.core.config import get_settings
    from app.api.posters import CREATOR_ROLES
    return templates.TemplateResponse("posters.html", {
        "request": request, "user": user,
        "can_create": user.role in CREATOR_ROLES,
        "max_upload_mb": get_settings().MAX_UPLOAD_MB,
        "hunt_goal": get_settings().POSTER_HUNT_GOAL,
    })
