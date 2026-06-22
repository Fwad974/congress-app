"""
Dubai Stem Cell Congress — Conference App
FastAPI Backend with Admin Dashboard
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import engine, Base
from app.models.user import User  # noqa – registers model
from app.models.audit_log import AuditLog, AuditAction, AuditSeverity  # noqa – registers model
from app.models.schedule import ScheduleItem, ScheduleType, ScheduleBookmark  # noqa – registers model
from app.models.notification import NotificationSettings, UserNotification, PushSubscription  # noqa – registers model
from app.models.note import Note  # noqa – registers model
from app.models.qa import Question, QuestionVote, QuestionStatus  # noqa – registers model
from app.core.realtime import broadcaster
from app.api import auth, pages
from app.api import admin as admin_api
from app.api import admin_pages
from app.api import schedule as schedule_api
from app.api import notifications as notifications_api
from app.api import notes as notes_api
from app.api import qa as qa_api

settings = get_settings()


def _sync_pg_enum(connection, type_name: str, python_enum):
    """Add any missing values from a Python enum to an existing Postgres enum type.

    Postgres doesn't update enum types when SQLAlchemy's create_all sees the
    table already exists, so newly added Python enum values would fail at
    insert time. This is a no-op on non-Postgres dialects.
    """
    existing = {
        row[0] for row in connection.execute(text(
            "SELECT enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = :name"
        ), {"name": type_name})
    }
    if not existing:
        # Type doesn't exist yet (first boot) — create_all will handle it.
        return
    for member in python_enum:
        if member.value in existing:
            continue
        # Values come from a Python Enum we control; safe to escape inline.
        # ALTER TYPE ... ADD VALUE cannot bind parameters and (pre-PG 12) cannot
        # run inside a transaction block, so use an autocommit connection.
        safe = member.value.replace("'", "''")
        connection.execute(text(
            f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{safe}'"
        ))


def _migrate_schedule_table(connection):
    """Bring the schedule_items table in line with the current model.

    create_all only creates missing tables/columns it knows about; it never
    drops or alters. We do two surgical changes by hand:

      - drop the legacy `speaker` column (now stored inside `extra`)
      - add the `extra` JSON column if missing

    Postgres-only; SQLite tests start from a clean DB each run.
    """
    cols = {
        row[0] for row in connection.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'schedule_items'"
        ))
    }
    if not cols:
        return  # table doesn't exist yet — create_all will build it fresh
    if "extra" not in cols:
        connection.execute(text(
            "ALTER TABLE schedule_items "
            "ADD COLUMN extra JSON NOT NULL DEFAULT '{}'::json"
        ))
    if "speaker" in cols:
        connection.execute(text(
            "ALTER TABLE schedule_items DROP COLUMN speaker"
        ))


@asynccontextmanager
async def lifespan(application: FastAPI):
    Base.metadata.create_all(bind=engine)

    # Sync Postgres enum types with current Python enum members.
    if engine.dialect.name == "postgresql":
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            _sync_pg_enum(conn, "auditaction", AuditAction)
            _sync_pg_enum(conn, "auditseverity", AuditSeverity)
            _sync_pg_enum(conn, "scheduletype", ScheduleType)
        with engine.begin() as conn:
            _migrate_schedule_table(conn)

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

    # Connect the realtime broadcaster (Redis if configured, else in-process).
    await broadcaster.connect()

    yield

    await broadcaster.disconnect()


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
app.include_router(notifications_api.router, prefix="/api/notifications", tags=["notifications"])
app.include_router(notes_api.router, prefix="/api/notes", tags=["notes"])
app.include_router(qa_api.router, prefix="/api", tags=["qa"])
