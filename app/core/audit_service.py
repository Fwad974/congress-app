"""
Audit Service — Write audit log entries
All admin actions must be logged per AUDIT-01.
"""
from typing import Optional
from fastapi import Request
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.audit_log import AuditLog, AuditAction, AuditSeverity
from app.core.admin_security import hash_ip, get_client_ip


# Actions that are critical severity
CRITICAL_ACTIONS = {
    AuditAction.user_delete,
    AuditAction.role_change,
    AuditAction.user_suspend,
    AuditAction.settings_change,
    AuditAction.export_data,
    AuditAction.bulk_import,
}

WARNING_ACTIONS = {
    AuditAction.login_failure,
    AuditAction.user_unlock,
    AuditAction.content_remove,
}


def log_action(
    db: Session,
    actor: User,
    action: AuditAction,
    description: str,
    request: Optional[Request] = None,
    target: Optional[User] = None,
    target_id: Optional[int] = None,
    target_email: Optional[str] = None,
    target_type: str = "user",
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
):
    """Create an immutable audit log entry."""
    severity = AuditSeverity.info
    if action in CRITICAL_ACTIONS:
        severity = AuditSeverity.critical
    elif action in WARNING_ACTIONS:
        severity = AuditSeverity.warning

    ip = None
    user_agent = None
    path = None
    if request:
        ip = hash_ip(get_client_ip(request))
        user_agent = (request.headers.get("user-agent", ""))[:500]
        path = str(request.url.path)[:500]

    entry = AuditLog(
        actor_id=actor.id,
        actor_email=actor.email,
        actor_role=actor.role.value,
        action=action,
        severity=severity,
        description=description,
        target_id=target.id if target else target_id,
        target_email=target.email if target else target_email,
        target_type=target_type,
        old_value=old_value,
        new_value=new_value,
        ip_hash=ip,
        user_agent=user_agent,
        request_path=path,
    )

    db.add(entry)
    db.commit()
    return entry


def log_system_action(
    db: Session,
    action: AuditAction,
    description: str,
    actor_id: int = 0,
    actor_email: str = "system",
    actor_role: str = "system",
    **kwargs,
):
    """Log a system-initiated action (no user context)."""
    entry = AuditLog(
        actor_id=actor_id,
        actor_email=actor_email,
        actor_role=actor_role,
        action=action,
        severity=AuditSeverity.info,
        description=description,
        **kwargs,
    )
    db.add(entry)
    db.commit()
    return entry
