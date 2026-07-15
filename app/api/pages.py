from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user_optional
from app.models.user import UserRole

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/logout")
async def logout_page():
    resp = RedirectResponse(url="/", status_code=302)
    for p in ["/", "/api", "/api/auth", "/home", "/profile", "/settings", "/certificates", "/admin", "/notes", "/qa", "/present"]:
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
    return templates.TemplateResponse("signup.html", {"request": request})


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user=Depends(get_current_user_optional)):
    if user:
        return RedirectResponse(url="/home", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})


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
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("certificates.html", {"request": request, "user": user})


@router.get("/schedule", response_class=HTMLResponse)
async def schedule_page(request: Request, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    is_admin = user.role in (UserRole.admin, UserRole.super_admin)
    return templates.TemplateResponse(
        "schedule.html",
        {"request": request, "user": user, "is_admin": is_admin},
    )


@router.get("/notes", response_class=HTMLResponse)
async def notes_page(request: Request, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("notes.html", {"request": request, "user": user})


@router.get("/qa/{session_id}", response_class=HTMLResponse)
async def qa_page(request: Request, session_id: int, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
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
