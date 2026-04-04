"""
Test configuration — SQLite database for fast, isolated tests.
Patches PostgreSQL ARRAY columns to use SQLite-compatible JSON type.
"""
import os
import json
import pytest
from unittest.mock import MagicMock

# Override DATABASE_URL before any app imports
os.environ["DATABASE_URL"] = "sqlite:///test.db"

from sqlalchemy import create_engine, Text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.types import TypeDecorator
from fastapi.testclient import TestClient

from app.core.database import Base, get_db
from app.core.security import hash_password, create_access_token
from app.models.user import User, UserRole
from app.models.audit_log import AuditLog
from app.core.admin_security import rate_limiter, login_tracker


# ─── SQLite ARRAY workaround ────────────────────────────────────
class JSONEncodedList(TypeDecorator):
    """Stores a Python list as a JSON string in SQLite."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return []
        return json.loads(value)


# Replace the ARRAY column type with our JSON-compatible type
User.__table__.c.research_interests.type = JSONEncodedList()


# SQLite engine
TEST_DATABASE_URL = "sqlite:///test.db"
engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Reset rate limiter and login tracker between tests."""
    rate_limiter._requests.clear()
    rate_limiter._blocked.clear()
    login_tracker._attempts.clear()
    login_tracker._locked.clear()
    login_tracker._permanently_locked.clear()
    yield


@pytest.fixture()
def db():
    """Provide a clean database session for each test."""
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db):
    """FastAPI TestClient with overridden DB dependency."""
    from app.main import app

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ─── User Factories ──────────────────────────────────────────────

def make_user(db, email="user@test.com", password="Test1234", role=UserRole.attendee,
              full_name="Test User", is_active=True, **kwargs):
    """Create a user in the database and return it."""
    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=full_name,
        role=role,
        is_active=is_active,
        research_interests=[],
        **kwargs,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def auth_cookie(user):
    """Generate an access_token cookie value for the given user."""
    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return {"access_token": token}


@pytest.fixture()
def attendee(db):
    return make_user(db, email="attendee@test.com", role=UserRole.attendee, full_name="Attendee")


@pytest.fixture()
def admin_user(db):
    return make_user(db, email="admin@test.com", role=UserRole.admin, full_name="Admin User")


@pytest.fixture()
def super_admin(db):
    return make_user(db, email="superadmin@test.com", role=UserRole.super_admin, full_name="Super Admin")


@pytest.fixture()
def moderator(db):
    return make_user(db, email="mod@test.com", role=UserRole.moderator, full_name="Moderator")
