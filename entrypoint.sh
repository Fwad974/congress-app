#!/bin/sh

# Auto-create super admin if env vars are set
if [ -n "$SUPER_ADMIN_EMAIL" ] && [ -n "$SUPER_ADMIN_PASSWORD" ]; then
    echo "Creating super admin from environment variables..."
    python -c "
import os
import sys
import traceback

try:
    from app.core.database import SessionLocal, engine, Base
    from app.core.security import hash_password
    from app.models.user import User, UserRole
    from app.models.audit_log import AuditLog  # register model

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        email = os.environ['SUPER_ADMIN_EMAIL']
        password = os.environ['SUPER_ADMIN_PASSWORD']
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            existing.role = UserRole.super_admin
            existing.hashed_password = hash_password(password)
            existing.is_active = True
            db.commit()
            print(f'Super admin {email} updated (password reset, role ensured)')
        else:
            user = User(
                email=email,
                hashed_password=hash_password(os.environ['SUPER_ADMIN_PASSWORD']),
                full_name=os.environ.get('SUPER_ADMIN_NAME', 'Super Admin'),
                institution=os.environ.get('SUPER_ADMIN_INSTITUTION'),
                role=UserRole.super_admin,
                is_active=True,
                is_verified=True,
                research_interests=[],
            )
            db.add(user)
            db.commit()
            print(f'Created super admin: {email}')
    finally:
        db.close()
except Exception as e:
    print(f'ERROR creating super admin: {e}', file=sys.stderr)
    traceback.print_exc()
" || echo "WARNING: Super admin creation failed, continuing anyway..."
fi

exec "$@"
