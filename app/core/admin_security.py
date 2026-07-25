"""
Admin Security Layer — Dubai Stem Cell Congress 2027
RBAC enforcement, rate limiting, audit logging, IP tracking
"""
import time
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from functools import wraps
from collections import defaultdict

from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User, UserRole


# ─── Role Hierarchy ───────────────────────────────────────────────
ROLE_HIERARCHY = {
    UserRole.super_admin: 100,
    UserRole.admin: 80,
    UserRole.moderator: 60,
    UserRole.review_chair: 50,
    UserRole.session_chair: 50,
    UserRole.speaker: 20,
    UserRole.reviewer: 20,
    UserRole.attendee: 10,
}

# Which roles can assign which other roles (ROLE-01, ROLE-02)
ROLE_ASSIGNMENT_MAP = {
    UserRole.super_admin: [
        UserRole.super_admin, UserRole.admin, UserRole.moderator,
        UserRole.review_chair, UserRole.session_chair,
        UserRole.speaker, UserRole.reviewer, UserRole.attendee,
    ],
    UserRole.admin: [
        UserRole.moderator, UserRole.review_chair, UserRole.session_chair,
        UserRole.speaker, UserRole.reviewer, UserRole.attendee,
    ],
}

# Admin roles that require 2FA (AUTH-01)
ADMIN_ROLES = {UserRole.super_admin, UserRole.admin, UserRole.moderator,
               UserRole.review_chair, UserRole.session_chair}

# Session timeout per role (AUTH-02)
SESSION_TIMEOUT = {
    'admin': 30,   # minutes
    'regular': 1440,  # 24h
}


# ─── Rate Limiter (in-memory, per-IP) ────────────────────────────
class RateLimiter:
    """Simple sliding-window rate limiter per IP."""
    def __init__(self):
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._blocked: dict[str, float] = {}  # IP -> unblock_time

    def is_blocked(self, ip: str) -> bool:
        if ip in self._blocked:
            if time.time() < self._blocked[ip]:
                return True
            del self._blocked[ip]
        return False

    def block(self, ip: str, seconds: int = 900):
        self._blocked[ip] = time.time() + seconds

    def check(self, ip: str, max_requests: int = 60, window: int = 60) -> bool:
        """Returns True if allowed, False if rate limited."""
        if self.is_blocked(ip):
            return False

        now = time.time()
        cutoff = now - window
        self._requests[ip] = [t for t in self._requests[ip] if t > cutoff]

        if len(self._requests[ip]) >= max_requests:
            return False

        self._requests[ip].append(now)
        return True


rate_limiter = RateLimiter()


# ─── Login Attempt Tracker (AUTH-03) ─────────────────────────────
class LoginTracker:
    """Track failed login attempts per email. 5 fails → 15 min lock. 10 → manual."""
    def __init__(self):
        self._attempts: dict[str, list[float]] = defaultdict(list)
        self._locked: dict[str, float] = {}  # email -> unlock_time
        self._permanently_locked: set[str] = set()

    def is_locked(self, email: str) -> tuple[bool, Optional[str]]:
        if email in self._permanently_locked:
            return True, "Account locked. Contact administrator."
        if email in self._locked:
            if time.time() < self._locked[email]:
                remaining = int(self._locked[email] - time.time())
                return True, f"Too many attempts. Try again in {remaining // 60 + 1} minutes."
            del self._locked[email]
        return False, None

    def _prune(self, now: float):
        # Bound memory: drop empty attempt windows and expired temporary locks.
        # (Permanent locks are few and cleared on admin unlock.)
        window = now - 1800
        for email in list(self._attempts):
            kept = [t for t in self._attempts[email] if t > window]
            if kept:
                self._attempts[email] = kept
            else:
                self._attempts.pop(email, None)
        for email in list(self._locked):
            if self._locked[email] <= now:
                self._locked.pop(email, None)

    def record_failure(self, email: str):
        now = time.time()
        # Opportunistically prune stale state so the dicts don't grow without
        # bound under a stream of failed logins for distinct emails.
        if len(self._attempts) > 512:
            self._prune(now)
        window = now - 1800  # 30 min window
        self._attempts[email] = [t for t in self._attempts[email] if t > window]
        self._attempts[email].append(now)

        count = len(self._attempts[email])
        if count >= 10:
            self._permanently_locked.add(email)
        elif count >= 5:
            self._locked[email] = now + 900  # 15 min

    def reset(self, email: str):
        self._attempts.pop(email, None)
        self._locked.pop(email, None)

    def unlock(self, email: str):
        """Manual unlock by admin."""
        self._permanently_locked.discard(email)
        self._locked.pop(email, None)
        self._attempts.pop(email, None)


login_tracker = LoginTracker()


# ─── CSRF Token Generator ────────────────────────────────────────
def generate_csrf_token() -> str:
    return secrets.token_hex(32)


def validate_csrf_token(request_token: str, session_token: str) -> bool:
    if not request_token or not session_token:
        return False
    return secrets.compare_digest(request_token, session_token)


# ─── Dependency: Require Minimum Role ─────────────────────────────
def require_role(min_role: UserRole):
    """FastAPI dependency that enforces minimum role level."""
    async def _check(request: Request, user: User = Depends(get_current_user)):
        user_level = ROLE_HIERARCHY.get(user.role, 0)
        required_level = ROLE_HIERARCHY.get(min_role, 0)

        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {min_role.value} or higher"
            )

        # Rate limit admin endpoints
        client_ip = request.client.host if request.client else "unknown"
        if not rate_limiter.check(client_ip, max_requests=120, window=60):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Try again later."
            )

        return user

    return _check


def require_super_admin():
    return require_role(UserRole.super_admin)


def require_admin():
    return require_role(UserRole.admin)


def require_moderator():
    return require_role(UserRole.moderator)


# ─── Helpers ──────────────────────────────────────────────────────
def can_assign_role(assigner: User, target_role: UserRole) -> bool:
    """Check if assigner can assign the target role (ROLE-01, ROLE-02)."""
    allowed = ROLE_ASSIGNMENT_MAP.get(assigner.role, [])
    return target_role in allowed


def can_manage_user(admin: User, target: User) -> bool:
    """Admin can manage users at or below their level (except themselves).
    Super Admin can manage other Super Admins.
    Admin can manage Moderator and below, but not other Admins."""
    if admin.id == target.id:
        return False  # Can't manage yourself
    admin_level = ROLE_HIERARCHY.get(admin.role, 0)
    target_level = ROLE_HIERARCHY.get(target.role, 0)
    # Super admin can manage everyone (including other super admins)
    if admin.role == UserRole.super_admin:
        return True
    return admin_level > target_level


# Global live moderators run Q&A/polls in ANY session. Session chairs moderate
# only sessions they're assigned to (see can_moderate_session).
GLOBAL_LIVE_MODERATOR_ROLES = {UserRole.moderator, UserRole.admin, UserRole.super_admin}
# Content moderators triage reports and remove user content (same tier).
MODERATOR_ROLES = GLOBAL_LIVE_MODERATOR_ROLES


def is_live_moderator(user: User) -> bool:
    """Global live moderator — may moderate Q&A/polls in any session."""
    return user.role in GLOBAL_LIVE_MODERATOR_ROLES


def is_moderator(user: User) -> bool:
    """Content moderator — may triage reports and remove flagged content."""
    return user.role in MODERATOR_ROLES


def can_moderate_session(db, user: User, session_id: int) -> bool:
    """Global moderators, plus the session's assigned chair or presenter."""
    if is_live_moderator(user):
        return True
    from app.models.schedule import ScheduleItem
    row = db.query(ScheduleItem.speaker_id, ScheduleItem.chair_id).filter(
        ScheduleItem.id == session_id).first()
    if not row:
        return False
    return user.id is not None and user.id in (row[0], row[1])


REVIEW_MANAGER_ROLES = {UserRole.review_chair, UserRole.admin, UserRole.super_admin}
ASSIGNABLE_REVIEWER_ROLES = {UserRole.reviewer, UserRole.review_chair}


def is_review_chair(user: User) -> bool:
    """Can this user manage the review process (assign reviewers, decide)?"""
    return user.role in REVIEW_MANAGER_ROLES


def is_assignable_reviewer(user: User) -> bool:
    return user.role in ASSIGNABLE_REVIEWER_ROLES


def hash_ip(ip: str) -> str:
    """One-way hash IP for audit logs."""
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
