from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import hash_password, verify_password, create_access_token, get_current_user
from app.core.admin_security import login_tracker, get_client_ip
from app.models.user import User
from app.models.audit_log import AuditLog, AuditAction
from app.core.audit_service import log_action
from app.schemas.user import SignUpRequest, LoginRequest, TokenResponse, UserResponse, ProfileUpdateRequest, PasswordChangeRequest

router = APIRouter()


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
    response.set_cookie(
        key="access_token", value=token, httponly=True,
        samesite="lax", path="/", max_age=86400,
    )
    return user


@router.post("/login", response_model=UserResponse)
def login(req: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    # Check if locked (AUTH-03)
    locked, msg = login_tracker.is_locked(req.email)
    if locked:
        raise HTTPException(status_code=429, detail=msg)

    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.hashed_password):
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
    response.set_cookie(
        key="access_token", value=token, httponly=True,
        samesite="lax", path="/", max_age=86400,
    )
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
    if not verify_password(req.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    user.hashed_password = hash_password(req.new_password)
    user.updated_at = datetime.now(timezone.utc)
    db.commit()

    log_action(db, user, AuditAction.password_change, "User changed their password", request=request)
    return {"message": "Password updated successfully"}


@router.delete("/account")
def delete_account(db: Session = Depends(get_db), user: User = Depends(get_current_user), response: Response = None):
    db.delete(user)
    db.commit()
    response = Response(status_code=200)
    response.delete_cookie("access_token")
    return {"message": "Account deleted"}
