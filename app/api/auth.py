import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import get_settings
from app.core.security import hash_password, verify_password, create_access_token, get_current_user
from app.core.admin_security import login_tracker, get_client_ip
from app.core.oauth import oauth, PROVIDERS, provider_enabled, enabled_providers, upsert_oauth_user
from app.models.user import User
from app.models.audit_log import AuditLog, AuditAction
from app.core.audit_service import log_action
from app.schemas.user import (SignUpRequest, LoginRequest, TokenResponse, UserResponse,
                              ProfileUpdateRequest, PasswordChangeRequest,
                              ForgotPasswordRequest, ResetPasswordRequest)

router = APIRouter()
logger = logging.getLogger(__name__)
settings = get_settings()


def set_auth_cookie(response: Response, token: str) -> None:
    """Single writer for the session cookie.

    `secure` follows ENVIRONMENT so production never sends the token over plain
    HTTP (local http://localhost dev still works), and `max_age` derives from
    ACCESS_TOKEN_EXPIRE_MINUTES so the cookie can't outlive the JWT.
    """
    cfg = get_settings()
    response.set_cookie(
        key="access_token", value=token, httponly=True,
        samesite="lax", path="/",
        max_age=cfg.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        secure=cfg.ENVIRONMENT.lower() == "production",
    )


@router.post("/signup", response_model=UserResponse, status_code=201)
def signup(req: SignUpRequest, response: Response, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=req.email,
        hashed_password=hash_password(req.password),
        full_name=req.full_name,
        institution=req.institution,
        research_interests=req.research_interests or [],
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    set_auth_cookie(response, token)
    return user


@router.post("/login", response_model=UserResponse)
def login(req: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    # Check if locked (AUTH-03)
    locked, msg = login_tracker.is_locked(req.email)
    if locked:
        raise HTTPException(status_code=429, detail=msg)

    user = db.query(User).filter(User.email == req.email).first()
    if not user or not user.hashed_password or not verify_password(req.password, user.hashed_password):
        login_tracker.record_failure(req.email)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account suspended")

    # Success — reset tracker
    login_tracker.reset(req.email)
    user.last_login = datetime.now(timezone.utc)
    db.commit()

    # Audit login
    log_action(db, user, AuditAction.login_success, f"Login from {get_client_ip(request)}", request=request)

    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    set_auth_cookie(response, token)
    return user


@router.post("/logout")
def logout_post():
    from fastapi.responses import JSONResponse
    resp = JSONResponse(content={"message": "Logged out"})
    for p in ["/", "/api", "/api/auth", "/home", "/profile", "/settings", "/certificates", "/admin"]:
        resp.set_cookie(key="access_token", value="deleted", path=p, max_age=0, httponly=True, samesite="lax")
        resp.set_cookie(key="access_token", value="deleted", path=p, max_age=0, httponly=False, samesite="lax")
        resp.set_cookie(key="access_token", value="deleted", path=p, max_age=0)
    return resp


@router.get("/logout")
def logout_get():
    from fastapi.responses import RedirectResponse
    resp = RedirectResponse(url="/", status_code=302)
    for p in ["/", "/api", "/api/auth", "/home", "/profile", "/settings", "/certificates", "/admin"]:
        resp.set_cookie(key="access_token", value="deleted", path=p, max_age=0, httponly=True, samesite="lax")
        resp.set_cookie(key="access_token", value="deleted", path=p, max_age=0, httponly=False, samesite="lax")
        resp.set_cookie(key="access_token", value="deleted", path=p, max_age=0)
    return resp


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    return user


# ─── OAuth / social login ────────────────────────────────────────
@router.get("/oauth/providers")
def oauth_providers():
    """Which social providers are configured — the login/signup pages use this
    to decide which buttons to show."""
    return {"providers": enabled_providers()}


def _redirect_uri(request: Request, provider: str) -> str:
    if settings.OAUTH_REDIRECT_BASE:
        return settings.OAUTH_REDIRECT_BASE.rstrip("/") + f"/api/auth/oauth/{provider}/callback"
    return str(request.url_for("oauth_callback", provider=provider))


@router.get("/oauth/{provider}/login")
async def oauth_login(provider: str, request: Request):
    if provider not in PROVIDERS or not provider_enabled(provider):
        raise HTTPException(status_code=404, detail="Provider not enabled")
    client = oauth.create_client(provider)
    return await client.authorize_redirect(request, _redirect_uri(request, provider))


@router.get("/oauth/{provider}/callback", name="oauth_callback")
async def oauth_callback(provider: str, request: Request, db: Session = Depends(get_db)):
    if provider not in PROVIDERS or not provider_enabled(provider):
        raise HTTPException(status_code=404, detail="Provider not enabled")
    client = oauth.create_client(provider)
    try:
        token = await client.authorize_access_token(request)
    except Exception as e:  # noqa: BLE001 - provider errors, denied consent, bad state
        logger.warning("OAuth callback failed for %s: %s", provider, e)
        return RedirectResponse(url="/login?error=oauth", status_code=302)

    claims = token.get("userinfo") or {}
    sub = claims.get("sub")
    if not sub:
        return RedirectResponse(url="/login?error=oauth", status_code=302)

    email = claims.get("email")
    email_verified = bool(claims.get("email_verified"))
    name = (claims.get("name")
            or " ".join(filter(None, [claims.get("given_name"), claims.get("family_name")]))
            or None)
    picture = claims.get("picture")

    user = upsert_oauth_user(db, provider, str(sub), email, name, email_verified, picture)
    if not user.is_active:
        return RedirectResponse(url="/login?error=suspended", status_code=302)

    log_action(db, user, AuditAction.login_success,
               f"OAuth login via {provider}", request=request)

    jwt_token = create_access_token({"sub": str(user.id), "role": user.role.value})
    resp = RedirectResponse(url="/home", status_code=302)
    set_auth_cookie(resp, jwt_token)
    return resp


@router.put("/profile", response_model=UserResponse)
def update_profile(req: ProfileUpdateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if req.full_name is not None:
        user.full_name = req.full_name
    if req.institution is not None:
        user.institution = req.institution
    if req.bio is not None:
        user.bio = req.bio
    if req.research_interests is not None:
        user.research_interests = req.research_interests
    if req.orcid_id is not None:
        user.orcid_id = req.orcid_id

    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


@router.put("/password")
def change_password(req: PasswordChangeRequest, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # OAuth-only accounts have no password to verify — guide them instead of
    # 500-ing on verify_password(None).
    if not user.hashed_password:
        raise HTTPException(status_code=400,
                            detail="This account signs in with a social provider and has no password to change.")
    if not verify_password(req.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    user.hashed_password = hash_password(req.new_password)
    user.updated_at = datetime.now(timezone.utc)
    db.commit()

    log_action(db, user, AuditAction.password_change, "User changed their password", request=request)
    return {"message": "Password updated successfully"}


@router.post("/password/forgot")
def forgot_password(req: ForgotPasswordRequest, request: Request,
                    db: Session = Depends(get_db)):
    """Start a password reset. Always returns the same response whether or not
    the email exists (no account enumeration). When a matching password account
    exists, a one-time link is emailed (or logged when SMTP is unconfigured)."""
    import hashlib
    import secrets as _secrets
    from datetime import timedelta
    from app.core.mailer import send_email
    cfg = get_settings()
    user = db.query(User).filter(User.email == req.email).first()
    if user and user.hashed_password:   # OAuth-only accounts have no password
        token = _secrets.token_urlsafe(32)
        user.reset_token_hash = hashlib.sha256(token.encode()).hexdigest()
        user.reset_token_expires = datetime.now(timezone.utc) + timedelta(
            minutes=cfg.RESET_TOKEN_TTL_MINUTES)
        db.commit()
        base = (cfg.OAUTH_REDIRECT_BASE.rstrip("/")
                if cfg.OAUTH_REDIRECT_BASE else str(request.base_url).rstrip("/"))
        link = f"{base}/reset-password?token={token}"
        send_email(user.email, "Reset your congress password",
                   f"Use this link within {cfg.RESET_TOKEN_TTL_MINUTES} minutes to "
                   f"reset your password:\n\n{link}\n\nIf you didn't request this, ignore this email.")
    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/password/reset")
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    import hashlib
    th = hashlib.sha256(req.token.encode()).hexdigest()
    user = db.query(User).filter(User.reset_token_hash == th).first()
    exp = getattr(user, "reset_token_expires", None) if user else None
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if not user or exp is None or exp < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")
    user.hashed_password = hash_password(req.new_password)
    user.reset_token_hash = None
    user.reset_token_expires = None
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "Password reset. You can now sign in."}


@router.delete("/account")
def delete_account(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    log_action(db, user, AuditAction.user_delete,
               "User deleted their own account (GDPR)", request=request,
               target_type="user", target_id=user.id)
    db.delete(user)
    db.commit()
    # Clear the cookie on every path the login flow may have set it.
    from fastapi.responses import JSONResponse
    resp = JSONResponse(content={"message": "Account deleted"})
    for p in ["/", "/api", "/api/auth", "/home", "/profile", "/settings", "/certificates", "/admin"]:
        resp.delete_cookie("access_token", path=p)
    resp.delete_cookie("access_token")
    return resp
