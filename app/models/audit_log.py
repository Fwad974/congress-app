"""
Audit Log Model — Immutable admin action log
Per AUDIT-01: who, what, when, target, old/new value, IP
Per AUDIT-02: Write-once, no edits
"""
import enum
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Text, Enum as SAEnum, Index
from app.core.database import Base


class AuditAction(str, enum.Enum):
    # User management
    user_create = "user_create"
    user_update = "user_update"
    user_delete = "user_delete"
    user_suspend = "user_suspend"
    user_activate = "user_activate"
    user_unlock = "user_unlock"
    role_change = "role_change"
    # Auth
    login_success = "login_success"
    login_failure = "login_failure"
    logout = "logout"
    password_change = "password_change"
    # Content
    content_approve = "content_approve"
    content_reject = "content_reject"
    content_flag = "content_flag"
    content_remove = "content_remove"
    # Schedule
    schedule_create = "schedule_create"
    schedule_update = "schedule_update"
    schedule_delete = "schedule_delete"
    # Notes
    note_delete = "note_delete"
    # Live Q&A
    question_moderate = "question_moderate"
    # Notifications
    broadcast_send = "broadcast_send"
    # Papers
    paper_decision = "paper_decision"
    # System
    settings_change = "settings_change"
    export_data = "export_data"
    bulk_import = "bulk_import"


class AuditSeverity(str, enum.Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)

    # Who
    actor_id = Column(Integer, nullable=False, index=True)
    actor_email = Column(String(255), nullable=False)
    actor_role = Column(String(50), nullable=False)

    # What
    action = Column(SAEnum(AuditAction), nullable=False, index=True)
    severity = Column(SAEnum(AuditSeverity), default=AuditSeverity.info, nullable=False)
    description = Column(Text, nullable=False)

    # Target
    target_id = Column(Integer, nullable=True, index=True)
    target_email = Column(String(255), nullable=True)
    target_type = Column(String(50), default="user")  # user, content, system

    # Changes
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)

    # Context
    ip_hash = Column(String(16), nullable=True)
    user_agent = Column(String(500), nullable=True)
    request_path = Column(String(500), nullable=True)

    # When (AUDIT-02: immutable — no updated_at)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        Index('idx_audit_actor_action', 'actor_id', 'action'),
        Index('idx_audit_created', 'created_at'),
    )
