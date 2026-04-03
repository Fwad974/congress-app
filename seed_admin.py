"""
Seed Script — Create First Super Admin
Run once after database setup:

    cd dubai-congress
    python seed_admin.py

This creates a super_admin account so you can access /admin
and manage all other users from the dashboard.
"""
import sys
from app.core.database import SessionLocal, engine, Base
from app.core.security import hash_password
from app.models.user import User, UserRole
from app.models.audit_log import AuditLog  # noqa - register model


def create_super_admin():
    # Create tables if they don't exist
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()

    try:
        # Check if any super_admin already exists
        existing = db.query(User).filter(User.role == UserRole.super_admin).first()
        if existing:
            print(f"\n  ⚠️  Super Admin already exists: {existing.email}")
            print(f"     Log in and go to /admin\n")
            return

        print("\n  🧬 Dubai Stem Cell Congress 2026 — First Admin Setup\n")
        print("  Create your Super Admin account:\n")

        email = input("  📧 Email: ").strip()
        if not email:
            print("  ✕ Email is required"); return

        # Check if email already exists
        existing_user = db.query(User).filter(User.email == email).first()
        if existing_user:
            # Promote existing user
            old_role = existing_user.role.value
            existing_user.role = UserRole.super_admin
            db.commit()
            print(f"\n  ✅ Promoted {email} from {old_role} → super_admin")
            print(f"  🔗 Log in and visit: http://localhost:8000/admin\n")
            return

        full_name = input("  👤 Full Name: ").strip()
        if not full_name:
            print("  ✕ Name is required"); return

        password = input("  🔒 Password (min 8 chars, uppercase + number): ").strip()
        if len(password) < 8:
            print("  ✕ Password too short"); return
        if not any(c.isupper() for c in password):
            print("  ✕ Need at least one uppercase letter"); return
        if not any(c.isdigit() for c in password):
            print("  ✕ Need at least one number"); return

        institution = input("  🏛️  Institution (optional): ").strip() or None

        user = User(
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
            institution=institution,
            role=UserRole.super_admin,
            is_active=True,
            is_verified=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        print(f"\n  ✅ Super Admin created: {email} (ID: {user.id})")
        print(f"  🔗 Start the server and visit: http://localhost:8000/admin")
        print(f"\n  To start the server:")
        print(f"     uvicorn app.main:app --reload --port 8000\n")

    except KeyboardInterrupt:
        print("\n  Cancelled.")
    except Exception as e:
        print(f"\n  ✕ Error: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    create_super_admin()
