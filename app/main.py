"""
Dubai Stem Cell Congress 2026 — Conference App
FastAPI Backend
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from app.core.database import engine, Base
from app.models.user import User  # noqa – registers model
from app.api import auth, pages


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

app.include_router(pages.router)
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
