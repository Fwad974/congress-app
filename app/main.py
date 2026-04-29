"""
Dubai Stem Cell Congress — Conference App
FastAPI Backend with Admin Dashboard
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from app.core.config import get_settings
from app.core.database import engine, Base
from app.models.user import User  # noqa – registers model
from app.models.audit_log import AuditLog  # noqa – registers model
from app.models.schedule import ScheduleItem  # noqa – registers model
from app.api import auth, pages
from app.api import admin as admin_api
from app.api import admin_pages
from app.api import schedule as schedule_api

settings = get_settings()


@asynccontextmanager
async def lifespan(application: FastAPI):
    Base.metadata.create_all(bind=engine)

    # Inject congress info into all Jinja2 templates as global variables
    from app.api.pages import templates as page_tpl
    from app.api.admin_pages import templates as admin_tpl
    congress = {
        "congress_name": settings.CONGRESS_NAME,
        "congress_year": settings.CONGRESS_YEAR,
        "congress_dates": settings.CONGRESS_DATES,
        "congress_venue": settings.CONGRESS_VENUE,
        "congress_deadline": settings.CONGRESS_DEADLINE,
        "congress_short": f"DSCC {settings.CONGRESS_YEAR}",
        "congress_full": f"{settings.CONGRESS_NAME} {settings.CONGRESS_YEAR}",
    }
    page_tpl.env.globals.update(congress)
    admin_tpl.env.globals.update(congress)

    yield


app = FastAPI(
    title=f"{settings.CONGRESS_NAME} {settings.CONGRESS_YEAR}",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Page routes
app.include_router(admin_pages.router)  # Must be before generic pages
app.include_router(pages.router)

# API routes
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(admin_api.router, prefix="/api/admin", tags=["admin"])
app.include_router(schedule_api.router, prefix="/api/schedule", tags=["schedule"])
