from pydantic_settings import BaseSettings
from functools import lru_cache


# The dev/test fallback signing key. It is intentionally public — the app
# refuses to boot with this value when ENVIRONMENT=production (see
# Settings.validate_production in app/main.py).
INSECURE_DEFAULT_SECRET = "09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7"


class Settings(BaseSettings):
    # "development" (default) allows the insecure fallback secret; set to
    # "production" in real deployments so a weak/missing SECRET_KEY fails fast.
    ENVIRONMENT: str = "development"

    DATABASE_URL: str = "postgresql://congress_user:congress_pass@localhost:5432/dubai_congress"
    SECRET_KEY: str = INSECURE_DEFAULT_SECRET
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24h for regular users

    # Congress info — edit in .env to change without touching code
    CONGRESS_NAME: str = "Dubai Stem Cell Congress"
    CONGRESS_YEAR: str = "2027"
    CONGRESS_DATES: str = "Feb 15 – 17, 2027"
    CONGRESS_VENUE: str = "Dubai World Trade Centre"
    CONGRESS_DEADLINE: str = "December 1, 2026"

    # Web Push (VAPID) — generate with `python gen_vapid_keys.py` and set in .env.
    # When either key is blank, web push is disabled and the app falls back to
    # the in-app notification feed (toasts) only.
    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""
    VAPID_SUBJECT: str = "mailto:admin@dubaicongress.example"
    PUSH_TTL: int = 86400  # seconds a push service should retain an undelivered message

    # Realtime (live Q&A / polls / reactions). Empty = in-process pub/sub only
    # (fine for a single worker / tests). Set to a redis:// URL to fan out
    # across workers and containers.
    REDIS_URL: str = ""

    # OAuth / social login. Each provider activates only when its client
    # id + secret are set. ORCID_ENV picks the sandbox vs production host.
    # OAUTH_REDIRECT_BASE overrides the callback origin behind a proxy
    # (e.g. https://app.example.com); empty = derive from the request.
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    ORCID_CLIENT_ID: str = ""
    ORCID_CLIENT_SECRET: str = ""
    ORCID_ENV: str = "sandbox"  # "sandbox" | "production"
    OAUTH_REDIRECT_BASE: str = ""

    # Paper file uploads. Relative path resolves under the app dir; in Docker
    # that's the persisted /app/data volume. Served only via an authenticated
    # download endpoint (never from /static).
    UPLOAD_DIR: str = "data/uploads"
    MAX_UPLOAD_MB: int = 15

    # Poster gallery scavenger hunt: number of distinct posters an attendee must
    # visit (check in to) before the hunt is marked complete.
    POSTER_HUNT_GOAL: int = 5
    # When true, posters uploaded by non-organizers require moderator approval
    # before appearing in the public gallery (upload-approval workflow).
    POSTER_APPROVAL_REQUIRED: bool = False
    # Mute duration for the MOD-04 escalation ladder (warn → mute → suspend).
    MUTE_HOURS: int = 24

    # Certificates. Attendance is recorded by scanning a session QR; each
    # attended session earns CME_CREDITS_PER_SESSION credits.
    CERT_MIN_SESSIONS: int = 3      # sessions for the attendance certificate
    CME_MIN_CREDITS: int = 5        # credits for the CME/CPD certificate
    CME_CREDITS_PER_SESSION: int = 1
    # Percentage-based eligibility for the attendance certificate. When > 0,
    # the goal becomes ceil(PCT% of non-break schedule items) instead of the
    # fixed CERT_MIN_SESSIONS (e.g. 80 = attend 80% of the program). With
    # parallel tracks, tune the percentage to what one person can attend.
    CERT_ATTENDANCE_PCT: int = 0

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
