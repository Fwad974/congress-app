"""
Admin API — User Management Dashboard
Secured with RBAC, rate limiting, and full audit logging.

Implements rules:
- AUTH-01: 2FA for admin roles (flagged)
- AUTH-03: Login attempt tracking
- ROLE-01/02: Role assignment hierarchy
- ROLE-04: Self-demotion prohibited
- MOD-04: Escalation ladder (warn → mute → suspend)
- AUDIT-01: All actions logged
- DATA-03: PII exports logged
"""
import csv
import io
import math
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, desc, asc

from app.core.database import get_db
from app.core.security import hash_password
from app.core.admin_security import (
    require_admin, require_super_admin, require_moderator,
    can_assign_role, can_manage_user, login_tracker,
    ROLE_HIERARCHY, get_client_ip, rate_limiter,
)
from app.core.audit_service import log_action
from app.models.user import User, UserRole
from app.models.audit_log import AuditLog, AuditAction, AuditSeverity
from app.models.note import Note
from app.models.schedule import ScheduleItem
from app.schemas.admin import (
    AdminUserCreate, AdminUserUpdate, AdminRoleChange, AdminUserSuspend,
    AdminBulkAction, AdminUserResponse, AdminUserListResponse,
    AuditLogResponse, AuditLogListResponse, DashboardStatsResponse,
)
from app.schemas.note import AdminNoteResponse, AdminNoteListResponse

router = APIRouter()


# ─── Dashboard Stats ──────────────────────────────────────────────
@router.get("/stats", response_model=DashboardStatsResponse)
def get_dashboard_stats(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin()),
):
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now.weekday())

    total = db.query(func.count(User.id)).scalar()
    active = db.query(func.count(User.id)).filter(User.is_active == True).scalar()
    suspended = total - active
    new_today = db.query(func.count(User.id)).filter(User.created_at >= today_start).scalar()
    new_week = db.query(func.count(User.id)).filter(User.created_at >= week_start).scalar()

    # Role breakdown
    role_counts = db.query(User.role, func.count(User.id)).group_by(User.role).all()
    roles_breakdown = {r.value: c for r, c in role_counts}

    # Recent signups
    recent_users = db.query(User).order_by(desc(User.created_at)).limit(5).all()

    # Recent audit activity
    recent_logs = db.query(AuditLog).order_by(desc(AuditLog.created_at)).limit(10).all()

    return DashboardStatsResponse(
        total_users=total,
        active_users=active,
        suspended_users=suspended,
        new_today=new_today,
        new_this_week=new_week,
        roles_breakdown=roles_breakdown,
        recent_signups=[AdminUserResponse.model_validate(u) for u in recent_users],
        recent_activity=[AuditLogResponse.model_validate(l) for l in recent_logs],
    )


# ─── List Users ───────────────────────────────────────────────────
@router.get("/users", response_model=AdminUserListResponse)
def list_users(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: str = Query(None),
    role: str = Query(None),
    status_filter: str = Query(None, alias="status"),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin()),
):
    q = db.query(User)

    # Filters
    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            User.full_name.ilike(like),
            User.email.ilike(like),
            User.institution.ilike(like),
        ))

    if role:
        try:
            q = q.filter(User.role == UserRole(role))
        except ValueError:
            pass

    if status_filter == "active":
        q = q.filter(User.is_active == True)
    elif status_filter == "suspended":
        q = q.filter(User.is_active == False)

    # Count
    total = q.count()

    # Sort
    sort_col = getattr(User, sort_by, User.created_at)
    q = q.order_by(desc(sort_col) if sort_order == "desc" else asc(sort_col))

    # Paginate
    offset = (page - 1) * per_page
    users = q.offset(offset).limit(per_page).all()

    return AdminUserListResponse(
        users=[AdminUserResponse.model_validate(u) for u in users],
        total=total,
        page=page,
        per_page=per_page,
        pages=max(1, math.ceil(total / per_page)),
    )


# ─── Get Single User ─────────────────────────────────────────────
@router.get("/users/{user_id}", response_model=AdminUserResponse)
def get_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin()),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return AdminUserResponse.model_validate(user)


# ─── Create User ─────────────────────────────────────────────────
@router.post("/users", response_model=AdminUserResponse, status_code=201)
def create_user(
    req: AdminUserCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin()),
):
    # Check email uniqueness
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    target_role = UserRole(req.role)
    if not can_assign_role(admin, target_role):
        raise HTTPException(
            status_code=403,
            detail=f"You cannot assign the {req.role} role"
        )

    user = User(
        email=req.email,
        hashed_password=hash_password(req.password),
        full_name=req.full_name,
        institution=req.institution,
        role=target_role,
        research_interests=req.research_interests or [],
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    log_action(
        db, admin, AuditAction.user_create,
        f"Created user {user.email} with role {user.role.value}",
        request=request, target=user, new_value=user.role.value,
    )

    return AdminUserResponse.model_validate(user)


# ─── Update User ─────────────────────────────────────────────────
@router.put("/users/{user_id}", response_model=AdminUserResponse)
def update_user(
    user_id: int,
    req: AdminUserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin()),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if admin.id != user.id and not can_manage_user(admin, user):
        raise HTTPException(status_code=403, detail="Cannot manage user at same or higher role")

    changes = []
    if req.full_name is not None and req.full_name != user.full_name:
        changes.append(f"name: {user.full_name} → {req.full_name}")
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
    db.flush()

    log_action(
        db, admin, AuditAction.user_update,
        f"Updated user {user.email}: {'; '.join(changes) if changes else 'profile fields'}",
        request=request, target=user,
    )
    db.refresh(user)

    return AdminUserResponse.model_validate(user)


# ─── Change Role ──────────────────────────────────────────────────
@router.put("/users/{user_id}/role", response_model=AdminUserResponse)
def change_role(
    user_id: int,
    req: AdminRoleChange,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin()),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # ROLE-04: Self-demotion prohibited
    if user.id == admin.id:
        raise HTTPException(status_code=403, detail="Cannot change your own role (ROLE-04)")

    new_role = UserRole(req.role)

    if not can_assign_role(admin, new_role):
        raise HTTPException(
            status_code=403,
            detail=f"You cannot assign the {req.role} role (ROLE-01/02)"
        )

    if not can_manage_user(admin, user):
        raise HTTPException(status_code=403, detail="Cannot manage user at same or higher role")

    old_role = user.role.value
    user.role = new_role
    user.updated_at = datetime.now(timezone.utc)
    db.flush()  # Write to DB but don't commit yet

    log_action(
        db, admin, AuditAction.role_change,
        f"Changed role for {user.email}: {old_role} → {new_role.value}. Reason: {req.reason or 'N/A'}",
        request=request, target=user,
        old_value=old_role, new_value=new_role.value,
    )
    # log_action already commits — now refresh to get clean state
    db.refresh(user)

    return AdminUserResponse.model_validate(user)


# ─── Suspend User ─────────────────────────────────────────────────
@router.post("/users/{user_id}/suspend", response_model=AdminUserResponse)
def suspend_user(
    user_id: int,
    req: AdminUserSuspend,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin()),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == admin.id:
        raise HTTPException(status_code=403, detail="Cannot suspend yourself")

    if not can_manage_user(admin, user):
        raise HTTPException(status_code=403, detail="Cannot manage user at same or higher role")

    user.is_active = False
    user.updated_at = datetime.now(timezone.utc)
    db.flush()

    log_action(
        db, admin, AuditAction.user_suspend,
        f"Suspended user {user.email}. Reason: {req.reason}. Duration: {req.duration_hours or 'indefinite'}h",
        request=request, target=user,
        old_value="active", new_value="suspended",
    )
    db.refresh(user)

    return AdminUserResponse.model_validate(user)


# ─── Activate User ────────────────────────────────────────────────
@router.post("/users/{user_id}/activate", response_model=AdminUserResponse)
def activate_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin()),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = True
    user.updated_at = datetime.now(timezone.utc)
    db.flush()

    # Also unlock login attempts
    login_tracker.unlock(user.email)

    log_action(
        db, admin, AuditAction.user_activate,
        f"Activated user {user.email}",
        request=request, target=user,
        old_value="suspended", new_value="active",
    )
    db.refresh(user)

    return AdminUserResponse.model_validate(user)


# ─── Delete User ──────────────────────────────────────────────────
@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_super_admin()),  # Only super admin can delete
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == admin.id:
        raise HTTPException(status_code=403, detail="Cannot delete yourself")

    # ROLE-01: Cannot delete other super admins unless you're the only one
    if user.role == UserRole.super_admin:
        sa_count = db.query(func.count(User.id)).filter(
            User.role == UserRole.super_admin
        ).scalar()
        if sa_count <= 1:
            raise HTTPException(status_code=403, detail="Cannot delete last super admin (ROLE-01)")

    email = user.email
    role = user.role.value

    log_action(
        db, admin, AuditAction.user_delete,
        f"Deleted user {email} (role: {role})",
        request=request, target_email=email, target_id=user_id,
        old_value=role,
    )

    db.delete(user)
    db.commit()

    return {"message": f"User {email} deleted"}


# ─── Bulk Actions ─────────────────────────────────────────────────
@router.post("/users/bulk")
def bulk_action(
    req: AdminBulkAction,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin()),
):
    results = {"success": 0, "failed": 0, "errors": []}

    for uid in req.user_ids:
        user = db.query(User).filter(User.id == uid).first()
        if not user:
            results["failed"] += 1
            results["errors"].append(f"User {uid} not found")
            continue

        if not can_manage_user(admin, user):
            results["failed"] += 1
            results["errors"].append(f"Cannot manage {user.email}")
            continue

        try:
            if req.action == "suspend":
                user.is_active = False
                log_action(db, admin, AuditAction.user_suspend,
                    f"Bulk suspend: {user.email}. Reason: {req.reason or 'bulk action'}",
                    request=request, target=user)
            elif req.action == "activate":
                user.is_active = True
                log_action(db, admin, AuditAction.user_activate,
                    f"Bulk activate: {user.email}", request=request, target=user)
            elif req.action == "role_change" and req.role:
                new_role = UserRole(req.role)
                if can_assign_role(admin, new_role):
                    old = user.role.value
                    user.role = new_role
                    log_action(db, admin, AuditAction.role_change,
                        f"Bulk role change: {user.email} {old} → {req.role}",
                        request=request, target=user, old_value=old, new_value=req.role)
                else:
                    results["failed"] += 1
                    results["errors"].append(f"Cannot assign {req.role} to {user.email}")
                    continue

            user.updated_at = datetime.now(timezone.utc)
            results["success"] += 1
        except Exception as e:
            results["failed"] += 1
            results["errors"].append(f"{user.email}: {str(e)}")

    db.commit()
    return results


# ─── Export Users (DATA-03: logged) ───────────────────────────────
@router.get("/users/export/csv")
def export_users_csv(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin()),
):
    users = db.query(User).order_by(User.created_at).all()

    log_action(
        db, admin, AuditAction.export_data,
        f"Exported {len(users)} users to CSV (DATA-03)",
        request=request,
        target_type="system",
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Email", "Name", "Institution", "Role", "Active", "Created"])
    for u in users:
        writer.writerow([
            u.id, u.email, u.full_name, u.institution or "",
            u.role.value, u.is_active, u.created_at.isoformat()
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=users_export.csv"}
    )


# ─── Audit Logs ───────────────────────────────────────────────────
@router.get("/audit", response_model=AuditLogListResponse)
def get_audit_logs(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    action: str = Query(None),
    severity: str = Query(None),
    actor_email: str = Query(None),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin()),
):
    q = db.query(AuditLog)

    if action:
        try:
            q = q.filter(AuditLog.action == AuditAction(action))
        except ValueError:
            pass

    if severity:
        try:
            q = q.filter(AuditLog.severity == AuditSeverity(severity))
        except ValueError:
            pass

    if actor_email:
        q = q.filter(AuditLog.actor_email.ilike(f"%{actor_email}%"))

    total = q.count()
    logs = q.order_by(desc(AuditLog.created_at)).offset(
        (page - 1) * per_page
    ).limit(per_page).all()

    return AuditLogListResponse(
        logs=[AuditLogResponse.model_validate(l) for l in logs],
        total=total,
        page=page,
        per_page=per_page,
    )


# ─── User Notes (super admin: view + delete) ─────────────────────
@router.get("/notes", response_model=AdminNoteListResponse)
def list_notes(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: str = Query(None),
    user_id: int = Query(None),
    db: Session = Depends(get_db),
    admin: User = Depends(require_super_admin()),
):
    q = db.query(Note, User).join(User, Note.user_id == User.id)

    if user_id:
        q = q.filter(Note.user_id == user_id)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            Note.content.ilike(like),
            User.full_name.ilike(like),
            User.email.ilike(like),
        ))

    total = q.count()
    rows = q.order_by(desc(Note.created_at)).offset(
        (page - 1) * per_page
    ).limit(per_page).all()

    # Resolve linked session titles in one query.
    item_ids = {n.schedule_item_id for n, _ in rows if n.schedule_item_id}
    titles = {}
    if item_ids:
        titles = {
            r[0]: r[1] for r in db.query(
                ScheduleItem.id, ScheduleItem.title
            ).filter(ScheduleItem.id.in_(item_ids)).all()
        }

    notes = [AdminNoteResponse(
        id=n.id, content=n.content, schedule_item_id=n.schedule_item_id,
        session_title=titles.get(n.schedule_item_id),
        created_at=n.created_at, updated_at=n.updated_at,
        user_id=u.id, user_name=u.full_name, user_email=u.email,
    ) for n, u in rows]

    return AdminNoteListResponse(
        notes=notes, total=total, page=page, per_page=per_page,
        pages=max(1, math.ceil(total / per_page)),
    )


@router.delete("/notes/{note_id}")
def delete_note(
    note_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_super_admin()),
):
    row = db.query(Note, User).join(User, Note.user_id == User.id).filter(
        Note.id == note_id
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Note not found")
    note, owner = row

    preview = (note.content or "")[:80]
    log_action(
        db, admin, AuditAction.note_delete,
        f"Deleted note #{note.id} by {owner.email}: \"{preview}\"",
        request=request, target=owner, target_type="note",
        old_value=preview,
    )

    db.delete(note)
    db.commit()
    return {"message": "Note deleted"}


# ─── Unlock Locked Account (AUTH-03) ─────────────────────────────
@router.post("/users/{user_id}/unlock")
def unlock_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin()),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    login_tracker.unlock(user.email)

    log_action(
        db, admin, AuditAction.user_unlock,
        f"Unlocked login for {user.email}",
        request=request, target=user,
    )

    return {"message": f"Login unlocked for {user.email}"}
