from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import get_settings

settings = get_settings()

# SQLite (tests) doesn't take pool sizing args; only pass them for real DBs.
_engine_kwargs = {
    # Recycle connections before a proxy/Postgres idle-timeout kills them, and
    # pre-ping so a stale connection is transparently replaced instead of
    # surfacing as a user-facing 500 after a DB restart.
    "pool_pre_ping": True,
    "pool_recycle": 1800,
}
if not settings.DATABASE_URL.startswith("sqlite"):
    # Headroom for a busy congress (hundreds of near-simultaneous connections)
    # without opening an unbounded number against Postgres.
    _engine_kwargs.update(pool_size=20, max_overflow=30)

engine = create_engine(settings.DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
