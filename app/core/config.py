from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://congress_user:congress_pass@localhost:5432/dubai_congress"
    SECRET_KEY: str = "09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7"
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

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
