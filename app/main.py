"""
Dubai Stem Cell Congress 2026 — Conference App
FastAPI Backend with Admin Dashboard
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from app.core.database import engine, Base
from app.models.user import User  # noqa – registers model
from app.models.audit_log import AuditLog  # noqa – registers model
from app.api import auth, pages
from app.api import admin as admin_api
from app.api import admin_pages


@asynccontextmanager
async def lifespan(application: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Dubai Stem Cell Congress 2026",
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
