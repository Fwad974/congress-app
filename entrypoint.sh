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

# ── Web Push (VAPID) keys ───────────────────────────────────────────
# Enable browser push out of the box. If keys weren't provided via the
# environment, load a previously persisted pair or generate a new one.
# Persisting keeps the public key stable across restarts — a changed key
# would invalidate every existing browser subscription.
VAPID_FILE="${VAPID_KEY_FILE:-/app/data/vapid.env}"

if [ -z "$VAPID_PUBLIC_KEY" ] || [ -z "$VAPID_PRIVATE_KEY" ]; then
    if [ -f "$VAPID_FILE" ]; then
        echo "Loading persisted VAPID keys from $VAPID_FILE"
        . "$VAPID_FILE"
    else
        echo "No VAPID keys set — generating a new pair for web push..."
        mkdir -p "$(dirname "$VAPID_FILE")" 2>/dev/null || true
        if python gen_vapid_keys.py --bare > "${VAPID_FILE}.tmp" 2>/dev/null; then
            mv "${VAPID_FILE}.tmp" "$VAPID_FILE"
            . "$VAPID_FILE"
            echo "Generated and persisted VAPID keys to $VAPID_FILE"
        else
            rm -f "${VAPID_FILE}.tmp"
            echo "WARNING: could not generate VAPID keys; web push stays disabled (in-app feed still works)"
        fi
    fi
    export VAPID_PUBLIC_KEY VAPID_PRIVATE_KEY
fi

if [ -n "$VAPID_PUBLIC_KEY" ] && [ -n "$VAPID_PRIVATE_KEY" ]; then
    echo "Web push enabled."
else
    echo "Web push disabled (no VAPID keys); notifications use the in-app feed."
fi

exec "$@"
