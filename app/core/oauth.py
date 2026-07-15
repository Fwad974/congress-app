"""
OAuth / social login.

- `oauth` is an Authlib registry; `init_oauth()` registers whichever providers
  are configured (Google and/or ORCID). Each activates only when its client id
  + secret are set, so the app runs fine with none configured.
- `upsert_oauth_user()` turns a verified provider profile into a local user,
  linking by verified email (per the chosen policy) and recording the link in
  oauth_accounts.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from authlib.integrations.starlette_client import OAuth
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.user import User, UserRole
from app.models.oauth import OAuthAccount

logger = logging.getLogger(__name__)
settings = get_settings()

PROVIDERS = ("google", "orcid")

oauth = OAuth()


def _orcid_base() -> str:
    return ("https://orcid.org" if settings.ORCID_ENV == "production"
            else "https://sandbox.orcid.org")


def provider_enabled(provider: str) -> bool:
    if provider == "google":
        return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)
    if provider == "orcid":
        return bool(settings.ORCID_CLIENT_ID and settings.ORCID_CLIENT_SECRET)
    return False


def enabled_providers() -> list[str]:
    return [p for p in PROVIDERS if provider_enabled(p)]


def init_oauth() -> None:
    """Register configured providers with the Authlib client (idempotent)."""
    if provider_enabled("google") and oauth.create_client("google") is None:
        oauth.register(
            name="google",
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    if provider_enabled("orcid") and oauth.create_client("orcid") is None:
        base = _orcid_base()
        oauth.register(
            name="orcid",
            client_id=settings.ORCID_CLIENT_ID,
            client_secret=settings.ORCID_CLIENT_SECRET,
            server_metadata_url=f"{base}/.well-known/openid-configuration",
            client_kwargs={"scope": "openid"},
        )


def _unique_email(db: Session, email: str) -> str:
    """Guard against the astronomically-unlikely placeholder-email collision."""
    candidate, n = email, 1
    while db.query(User.id).filter(User.email == candidate).first():
        candidate = f"{n}.{email}"
        n += 1
    return candidate


def upsert_oauth_user(
    db: Session,
    provider: str,
    sub: str,
    email: Optional[str] = None,
    name: Optional[str] = None,
    email_verified: bool = False,
    picture: Optional[str] = None,
) -> User:
    """Find (or create) the local user for a provider profile and record the link.

    Linking policy: if the provider gives a *verified* email that matches an
    existing account, log into that account. Otherwise create a fresh account
    keyed by the provider's stable id.
    """
    # 1. Already linked → that user.
    acct = db.query(OAuthAccount).filter(
        OAuthAccount.provider == provider,
        OAuthAccount.provider_user_id == sub,
    ).first()
    if acct:
        user = db.query(User).filter(User.id == acct.user_id).first()
        if user:
            _apply_profile(user, provider, sub, picture)
            db.commit()
            db.refresh(user)
            return user
        db.delete(acct)
        db.flush()

    # 2. Link by verified email.
    user = None
    if email and email_verified:
        user = db.query(User).filter(User.email == email).first()

    # 3. Otherwise create.
    if user is None:
        display = name or (email.split("@")[0] if email else f"{provider.title()} User")
        user_email = _unique_email(db, email or f"{sub}@{provider}.local")
        user = User(
            email=user_email,
            hashed_password=None,
            full_name=display,
            role=UserRole.attendee,
            is_active=True,
            is_verified=bool(email_verified),
            research_interests=[],
        )
        db.add(user)
        db.flush()

    _apply_profile(user, provider, sub, picture)
    db.add(OAuthAccount(user_id=user.id, provider=provider, provider_user_id=sub))
    db.commit()
    db.refresh(user)
    return user


def _apply_profile(user: User, provider: str, sub: str, picture: Optional[str]) -> None:
    if provider == "orcid" and not user.orcid_id:
        user.orcid_id = sub
    if picture and not user.profile_photo_url:
        user.profile_photo_url = picture
    user.last_login = datetime.now(timezone.utc)
