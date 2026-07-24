"""
Feature-flag registry, cache, and enforcement helpers.

The DB (``feature_flags`` table) is the source of truth; a small in-process
cache is loaded at startup and updated on every toggle so ``is_enabled`` /
``feature_enabled`` are cheap to call from templates and request handlers.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User, UserRole
from app.models.feature_flag import FeatureFlag

# key -> (label, description). This is the catalogue shown in the Releases tab.
FEATURES: Dict[str, tuple] = {
    "papers":  ("Abstracts & Papers",
                "Paper submission, peer review, and the accepted-papers showcase."),
    "posters": ("Poster Gallery",
                "Poster hall with voting, comments, and the scavenger hunt."),
    "notes":   ("My Notes", "Personal note-taking for attendees."),
    "qa":      ("Live Q&A", "Audience Q&A during sessions."),
    "polls":   ("Live Polls", "Live polls and word clouds during sessions."),
    "connect": ("Networking Directory",
                "Opt-in attendee directory for finding and contacting peers."),
    "sponsors": ("Sponsors & Exhibitors",
                 "Sponsor tiers, virtual exhibitor booths, and opt-in lead capture."),
    "feedback": ("Feedback & Ratings",
                 "Session star ratings, the post-event survey, and organizer sentiment."),
}
DEFAULT_ENABLED = True
_ADMIN_ROLES = {UserRole.admin, UserRole.super_admin}

_cache: Dict[str, bool] = {}


def ensure_seeded(db: Session) -> None:
    """Create any missing flag rows (default on), then warm the cache."""
    existing = {f.key for f in db.query(FeatureFlag).all()}
    added = False
    for key in FEATURES:
        if key not in existing:
            db.add(FeatureFlag(key=key, enabled=DEFAULT_ENABLED))
            added = True
    if added:
        db.commit()
    load_cache(db)


def load_cache(db: Session) -> None:
    _cache.clear()
    for f in db.query(FeatureFlag).all():
        _cache[f.key] = f.enabled


def is_enabled(key: str) -> bool:
    # Unknown keys fail open so non-gated areas are never accidentally hidden.
    return _cache.get(key, DEFAULT_ENABLED)


def set_enabled(db: Session, key: str, enabled: bool,
                user_id: Optional[int] = None) -> None:
    f = db.query(FeatureFlag).filter(FeatureFlag.key == key).first()
    if not f:
        f = FeatureFlag(key=key)
        db.add(f)
    f.enabled = enabled
    f.updated_by = user_id
    f.updated_at = datetime.now(timezone.utc)
    db.commit()
    _cache[key] = enabled


def all_flags(db: Session) -> List[dict]:
    rows = {f.key: f for f in db.query(FeatureFlag).all()}
    out = []
    for key, (label, desc) in FEATURES.items():
        f = rows.get(key)
        out.append({
            "key": key, "label": label, "description": desc,
            "enabled": f.enabled if f else DEFAULT_ENABLED,
            "updated_at": f.updated_at if f else None,
        })
    return out


def require_feature(key: str):
    """Router/route dependency: 403 for non-admins when the feature is off."""
    def _dep(user: User = Depends(get_current_user)) -> User:
        if not is_enabled(key) and user.role not in _ADMIN_ROLES:
            raise HTTPException(status_code=403,
                                detail="This feature isn't available right now")
        return user
    return _dep
