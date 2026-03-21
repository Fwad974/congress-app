from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user_optional

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/logout")
async def logout_page():
    """Top-level logout — clears cookie and redirects to landing."""
    resp = RedirectResponse(url="/", status_code=302)
    for p in ["/", "/api", "/api/auth", "/home", "/profile", "/settings", "/certificates"]:
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
