"""
Admin Page Routes — Serves the User Management Dashboard UI
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user_optional
from app.core.admin_security import ROLE_HIERARCHY
from app.models.user import UserRole

router = APIRouter()
import os as _os
_TPL_DIR = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "templates")
templates = Jinja2Templates(directory=_TPL_DIR)
templates.env.globals.setdefault("feature_enabled", lambda key: True)

ADMIN_MIN_LEVEL = ROLE_HIERARCHY[UserRole.admin]


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    user_level = ROLE_HIERARCHY.get(user.role, 0)
    if user_level < ADMIN_MIN_LEVEL:
        return RedirectResponse(url="/home", status_code=302)

    response = templates.TemplateResponse("admin_dashboard.html", {
        "request": request,
        "user": user,
    })
    # Prevent browser caching of admin pages
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response
