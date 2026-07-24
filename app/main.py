"""
Dubai Stem Cell Congress — Conference App
FastAPI Backend with Admin Dashboard
"""
from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import engine, Base
from app.models.user import User  # noqa – registers model
from app.models.audit_log import AuditLog, AuditAction, AuditSeverity  # noqa – registers model
from app.models.schedule import ScheduleItem, ScheduleType, ScheduleBookmark  # noqa – registers model
from app.models.notification import NotificationSettings, UserNotification, PushSubscription, Broadcast  # noqa – registers model
from app.models.note import Note  # noqa – registers model
from app.models.qa import Question, QuestionVote, QuestionStatus  # noqa – registers model
from app.models.poll import Poll, PollOption, PollResponse, PollType, PollStatus  # noqa – registers model
from app.models.oauth import OAuthAccount  # noqa – registers model
from app.models.paper import Paper, Review, PaperStatus  # noqa – registers model
from app.models.poster import (  # noqa – registers model
    Poster, PosterVote, PosterComment, PosterVisit,
)
from app.models.feature_flag import FeatureFlag  # noqa – registers model
from app.models.attendance import SessionAttendance  # noqa – registers model
from app.models.certificate import IssuedCertificate  # noqa – registers model
from app.models.sponsor import Sponsor, SponsorLead, SponsorVisit  # noqa – registers model
from app.models.feedback import SessionRating, SurveyResponse  # noqa – registers model
from app.models.venue import VenueRoom, VenueSetting  # noqa – registers model
from app.models.report import ContentReport  # noqa – registers model
from app.models.moderation import ModerationAction  # noqa – registers model
from app.core.realtime import broadcaster
from app.core.oauth import init_oauth
from app.api import auth, pages
from app.api import admin as admin_api
from app.api import admin_pages
from app.api import schedule as schedule_api
from app.api import notifications as notifications_api
from app.api import notes as notes_api
from app.api import qa as qa_api
from app.api import polls as polls_api
from app.api import reactions as reactions_api
from app.api import papers as papers_api
from app.api import posters as posters_api
from app.api import certificates as certificates_api
from app.api import connect as connect_api
from app.api import moderation as moderation_api
from app.api import sponsors as sponsors_api
from app.api import feedback as feedback_api
from app.api import venue as venue_api
from app.core import feature_flags

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
    if "speaker_id" not in cols:
        connection.execute(text(
            "ALTER TABLE schedule_items ADD COLUMN speaker_id INTEGER "
            "REFERENCES users(id) ON DELETE SET NULL"
        ))
    if "attend_code" not in cols:
        connection.execute(text(
            "ALTER TABLE schedule_items ADD COLUMN attend_code VARCHAR(12) UNIQUE"
        ))
    if "chair_id" not in cols:
        connection.execute(text(
            "ALTER TABLE schedule_items ADD COLUMN chair_id INTEGER "
            "REFERENCES users(id) ON DELETE SET NULL"
        ))
    if "speaker" in cols:
        connection.execute(text(
            "ALTER TABLE schedule_items DROP COLUMN speaker"
        ))


def _migrate_users_table(connection):
    """Make users.hashed_password nullable for OAuth-only accounts.

    Postgres-only; SQLite tests build the table fresh with the current model.
    """
    row = connection.execute(text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'users' AND column_name = 'hashed_password'"
    )).first()
    if row and row[0] == "NO":
        connection.execute(text(
            "ALTER TABLE users ALTER COLUMN hashed_password DROP NOT NULL"
        ))
    cols = {
        row[0] for row in connection.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'users'"
        ))
    }
    if cols and "networking_visible" not in cols:
        connection.execute(text(
            "ALTER TABLE users ADD COLUMN networking_visible BOOLEAN "
            "NOT NULL DEFAULT FALSE"
        ))
    if cols and "muted_until" not in cols:
        connection.execute(text(
            "ALTER TABLE users ADD COLUMN muted_until TIMESTAMPTZ"
        ))


def _migrate_papers_table(connection):
    """Add the manuscript-upload columns to an existing papers table.

    Postgres-only; SQLite tests build the table fresh with the current model.
    """
    cols = {
        row[0] for row in connection.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'papers'"
        ))
    }
    if not cols:
        return  # table doesn't exist yet — create_all will build it fresh
    if "file_name" not in cols:
        connection.execute(text(
            "ALTER TABLE papers ADD COLUMN file_name VARCHAR(255)"
        ))
    if "stored_file" not in cols:
        connection.execute(text(
            "ALTER TABLE papers ADD COLUMN stored_file VARCHAR(255)"
        ))
    if "review_deadline" not in cols:
        connection.execute(text(
            "ALTER TABLE papers ADD COLUMN review_deadline DATE"
        ))
    if "decision_score" not in cols:
        connection.execute(text(
            "ALTER TABLE papers ADD COLUMN decision_score INTEGER"
        ))
    if "decision_score_reason" not in cols:
        connection.execute(text(
            "ALTER TABLE papers ADD COLUMN decision_score_reason TEXT"
        ))


def _migrate_broadcasts_table(connection):
    """Add the audience_summary column to an existing broadcasts table."""
    cols = {
        row[0] for row in connection.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'broadcasts'"
        ))
    }
    if cols and "audience_summary" not in cols:
        connection.execute(text(
            "ALTER TABLE broadcasts ADD COLUMN audience_summary VARCHAR(500)"
        ))


def _migrate_reviews_table(connection):
    """Add the reviewer-response columns to an existing reviews table.

    Postgres-only; SQLite tests build the table fresh with the current model.
    """
    cols = {
        row[0] for row in connection.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'reviews'"
        ))
    }
    if not cols:
        return  # table doesn't exist yet — create_all will build it fresh
    if "state" not in cols:
        connection.execute(text(
            "ALTER TABLE reviews ADD COLUMN state VARCHAR(20) "
            "NOT NULL DEFAULT 'invited'"
        ))
    if "response_reason" not in cols:
        connection.execute(text(
            "ALTER TABLE reviews ADD COLUMN response_reason TEXT"
        ))
    if "rubric" not in cols:
        connection.execute(text(
            "ALTER TABLE reviews ADD COLUMN rubric JSONB"
        ))


def _migrate_reports_table(connection):
    """Add the auto-flag columns to an existing content_reports table."""
    cols = {
        row[0] for row in connection.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'content_reports'"
        ))
    }
    if not cols:
        return
    if "source" not in cols:
        connection.execute(text(
            "ALTER TABLE content_reports ADD COLUMN source VARCHAR(10) "
            "NOT NULL DEFAULT 'user'"
        ))
    # reporter_id becomes nullable so system auto-flags have no human reporter.
    row = connection.execute(text(
        "SELECT is_nullable FROM information_schema.columns WHERE "
        "table_name = 'content_reports' AND column_name = 'reporter_id'"
    )).first()
    if row and row[0] == "NO":
        connection.execute(text(
            "ALTER TABLE content_reports ALTER COLUMN reporter_id DROP NOT NULL"
        ))


def _migrate_posters_table(connection):
    """Add the approval-status column to an existing posters table."""
    cols = {
        row[0] for row in connection.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'posters'"
        ))
    }
    if cols and "status" not in cols:
        connection.execute(text(
            "ALTER TABLE posters ADD COLUMN status VARCHAR(12) "
            "NOT NULL DEFAULT 'approved'"
        ))


def _check_production_secrets():
    """Refuse to boot a production deployment with a weak/default signing key.

    SECRET_KEY signs both the JWT auth cookie and the OAuth session cookie, so a
    known value would let anyone forge admin tokens. Only enforced when
    ENVIRONMENT=production; development/test keep the convenience default.
    """
    from app.core.config import INSECURE_DEFAULT_SECRET, get_settings
    cfg = get_settings()
    if cfg.ENVIRONMENT.lower() != "production":
        return
    key = cfg.SECRET_KEY or ""
    if key == INSECURE_DEFAULT_SECRET or len(key) < 32:
        raise RuntimeError(
            "SECRET_KEY is missing or insecure but ENVIRONMENT=production. "
            "Set a strong, unique SECRET_KEY (e.g. `python -c \"import secrets; "
            "print(secrets.token_hex(32))\"`) before starting the app."
        )


@asynccontextmanager
async def lifespan(application: FastAPI):
    _check_production_secrets()
    Base.metadata.create_all(bind=engine)

    # Sync Postgres enum types with current Python enum members.
    if engine.dialect.name == "postgresql":
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            _sync_pg_enum(conn, "auditaction", AuditAction)
            _sync_pg_enum(conn, "auditseverity", AuditSeverity)
            _sync_pg_enum(conn, "scheduletype", ScheduleType)
        with engine.begin() as conn:
            _migrate_schedule_table(conn)
            _migrate_users_table(conn)
            _migrate_papers_table(conn)
            _migrate_reviews_table(conn)
            _migrate_broadcasts_table(conn)
            _migrate_reports_table(conn)
            _migrate_posters_table(conn)

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

    # Seed + cache feature flags, and expose feature_enabled() to templates
    # so nav links and pages can hide unreleased modules.
    from app.core import feature_flags
    from app.core.database import SessionLocal
    with SessionLocal() as fdb:
        feature_flags.ensure_seeded(fdb)
    page_tpl.env.globals["feature_enabled"] = feature_flags.is_enabled
    admin_tpl.env.globals["feature_enabled"] = feature_flags.is_enabled

    # Register configured OAuth providers (Google / ORCID).
    init_oauth()

    # Connect the realtime broadcaster (Redis if configured, else in-process).
    await broadcaster.connect()

    yield

    await broadcaster.disconnect()


app = FastAPI(
    title=f"{settings.CONGRESS_NAME} {settings.CONGRESS_YEAR}",
    version="1.0.0",
    lifespan=lifespan,
)

# Signed session cookie — holds the OAuth state/nonce during the redirect dance.
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    same_site="lax",
    https_only=False,  # set True behind HTTPS in production
    max_age=600,       # the OAuth handshake is short-lived
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
app.include_router(notes_api.router, prefix="/api/notes", tags=["notes"],
                   dependencies=[Depends(feature_flags.require_feature("notes"))])
app.include_router(qa_api.router, prefix="/api", tags=["qa"],
                   dependencies=[Depends(feature_flags.require_feature("qa"))])
app.include_router(polls_api.router, prefix="/api", tags=["polls"],
                   dependencies=[Depends(feature_flags.require_feature("polls"))])
app.include_router(reactions_api.router, prefix="/api", tags=["reactions"])
app.include_router(papers_api.router, prefix="/api/papers", tags=["papers"],
                   dependencies=[Depends(feature_flags.require_feature("papers"))])
app.include_router(posters_api.router, prefix="/api/posters", tags=["posters"],
                   dependencies=[Depends(feature_flags.require_feature("posters"))])
app.include_router(certificates_api.router, prefix="/api", tags=["certificates"])
app.include_router(connect_api.router, prefix="/api/connect", tags=["connect"],
                   dependencies=[Depends(feature_flags.require_feature("connect"))])
app.include_router(moderation_api.router, prefix="/api/moderation", tags=["moderation"])
app.include_router(sponsors_api.router, prefix="/api/sponsors", tags=["sponsors"],
                   dependencies=[Depends(feature_flags.require_feature("sponsors"))])
app.include_router(feedback_api.router, prefix="/api", tags=["feedback"],
                   dependencies=[Depends(feature_flags.require_feature("feedback"))])
app.include_router(venue_api.router, prefix="/api/venue", tags=["venue"],
                   dependencies=[Depends(feature_flags.require_feature("venue"))])
