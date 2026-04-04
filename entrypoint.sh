#!/bin/sh
set -e

# Auto-create super admin if env vars are set
if [ -n "$SUPER_ADMIN_EMAIL" ] && [ -n "$SUPER_ADMIN_PASSWORD" ]; then
    echo "Creating super admin from environment variables..."
    python -c "
import sys
from app.core.database import SessionLocal, engine, Base
from app.core.security import hash_password
from app.models.user import User, UserRole
from app.models.audit_log import AuditLog  # register model
import os

Base.metadata.create_all(bind=engine)
db = SessionLocal()
try:
    email = os.environ['SUPER_ADMIN_EMAIL']
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        if existing.role != UserRole.super_admin:
            existing.role = UserRole.super_admin
            db.commit()
            print(f'Promoted {email} to super_admin')
        else:
            print(f'Super admin {email} already exists, skipping')
    else:
        user = User(
            email=email,
            hashed_password=hash_password(os.environ['SUPER_ADMIN_PASSWORD']),
            full_name=os.environ.get('SUPER_ADMIN_NAME', 'Super Admin'),
            institution=os.environ.get('SUPER_ADMIN_INSTITUTION'),
            role=UserRole.super_admin,
            is_active=True,
            is_verified=True,
        )
        db.add(user)
        db.commit()
        print(f'Created super admin: {email}')
finally:
    db.close()
"
fi

exec "$@"
