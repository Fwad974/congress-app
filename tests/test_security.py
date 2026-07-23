"""
Unit tests for security modules: password hashing, JWT, rate limiter, login tracker, RBAC helpers.
"""
import time
import pytest
from unittest.mock import MagicMock

from app.core.security import hash_password, verify_password, create_access_token, decode_token
from app.core.admin_security import (
    RateLimiter, LoginTracker, can_assign_role, can_manage_user,
    hash_ip, generate_csrf_token, validate_csrf_token,
    ROLE_HIERARCHY, ROLE_ASSIGNMENT_MAP, ADMIN_ROLES,
)
from app.models.user import UserRole


# ─── Password Hashing ────────────────────────────────────────────

class TestPasswordHashing:
    def test_hash_and_verify(self):
        hashed = hash_password("MyPass123")
        assert hashed != "MyPass123"
        assert verify_password("MyPass123", hashed)

    def test_wrong_password_fails(self):
        hashed = hash_password("MyPass123")
        assert not verify_password("WrongPass", hashed)

    def test_different_hashes_for_same_password(self):
        h1 = hash_password("Same123")
        h2 = hash_password("Same123")
        assert h1 != h2  # bcrypt salts differ


# ─── JWT Tokens ──────────────────────────────────────────────────

class TestJWT:
    def test_create_and_decode_token(self):
        token = create_access_token({"sub": "42", "role": "admin"})
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == "42"
        assert payload["role"] == "admin"
        assert "exp" in payload

    def test_invalid_token_returns_none(self):
        assert decode_token("garbage.token.here") is None

    def test_empty_token_returns_none(self):
        assert decode_token("") is None


# ─── Rate Limiter ─────────────────────────────────────────────────

class TestRateLimiter:
    def test_allows_under_limit(self):
        rl = RateLimiter()
        for _ in range(5):
            assert rl.check("1.2.3.4", max_requests=10, window=60)

    def test_blocks_over_limit(self):
        rl = RateLimiter()
        for _ in range(10):
            rl.check("1.2.3.4", max_requests=10, window=60)
        assert not rl.check("1.2.3.4", max_requests=10, window=60)

    def test_different_ips_independent(self):
        rl = RateLimiter()
        for _ in range(10):
            rl.check("ip1", max_requests=10, window=60)
        # ip1 blocked, ip2 should be fine
        assert not rl.check("ip1", max_requests=10, window=60)
        assert rl.check("ip2", max_requests=10, window=60)

    def test_block_and_unblock(self):
        rl = RateLimiter()
        rl.block("1.2.3.4", seconds=1)
        assert rl.is_blocked("1.2.3.4")
        time.sleep(1.1)
        assert not rl.is_blocked("1.2.3.4")

    def test_blocked_ip_rejected(self):
        rl = RateLimiter()
        rl.block("1.2.3.4", seconds=60)
        assert not rl.check("1.2.3.4")


# ─── Login Tracker ────────────────────────────────────────────────

class TestLoginTracker:
    def test_not_locked_initially(self):
        lt = LoginTracker()
        locked, msg = lt.is_locked("test@example.com")
        assert not locked
        assert msg is None

    def test_locks_after_5_failures(self):
        lt = LoginTracker()
        for _ in range(5):
            lt.record_failure("test@example.com")
        locked, msg = lt.is_locked("test@example.com")
        assert locked
        assert "minutes" in msg.lower() or "try again" in msg.lower()

    def test_permanent_lock_after_10_failures(self):
        lt = LoginTracker()
        for _ in range(10):
            lt.record_failure("test@example.com")
        locked, msg = lt.is_locked("test@example.com")
        assert locked
        assert "contact" in msg.lower()

    def test_reset_clears_lockout(self):
        lt = LoginTracker()
        for _ in range(5):
            lt.record_failure("test@example.com")
        lt.reset("test@example.com")
        locked, _ = lt.is_locked("test@example.com")
        assert not locked

    def test_unlock_clears_permanent_lock(self):
        lt = LoginTracker()
        for _ in range(10):
            lt.record_failure("test@example.com")
        lt.unlock("test@example.com")
        locked, _ = lt.is_locked("test@example.com")
        assert not locked


# ─── RBAC Helpers ─────────────────────────────────────────────────

def _mock_user(role, user_id=1):
    u = MagicMock()
    u.role = role
    u.id = user_id
    return u


class TestCanAssignRole:
    def test_super_admin_can_assign_any(self):
        sa = _mock_user(UserRole.super_admin)
        for role in UserRole:
            assert can_assign_role(sa, role), f"super_admin should assign {role}"

    def test_admin_can_assign_moderator_and_below(self):
        admin = _mock_user(UserRole.admin)
        assert can_assign_role(admin, UserRole.moderator)
        assert can_assign_role(admin, UserRole.attendee)
        assert can_assign_role(admin, UserRole.speaker)

    def test_admin_cannot_assign_admin(self):
        admin = _mock_user(UserRole.admin)
        assert not can_assign_role(admin, UserRole.admin)

    def test_admin_cannot_assign_super_admin(self):
        admin = _mock_user(UserRole.admin)
        assert not can_assign_role(admin, UserRole.super_admin)

    def test_moderator_cannot_assign_any(self):
        mod = _mock_user(UserRole.moderator)
        assert not can_assign_role(mod, UserRole.attendee)

    def test_attendee_cannot_assign_any(self):
        att = _mock_user(UserRole.attendee)
        assert not can_assign_role(att, UserRole.attendee)


class TestCanManageUser:
    def test_cannot_manage_self(self):
        admin = _mock_user(UserRole.admin, user_id=1)
        assert not can_manage_user(admin, admin)

    def test_super_admin_manages_everyone(self):
        sa = _mock_user(UserRole.super_admin, user_id=1)
        admin = _mock_user(UserRole.admin, user_id=2)
        attendee = _mock_user(UserRole.attendee, user_id=3)
        assert can_manage_user(sa, admin)
        assert can_manage_user(sa, attendee)

    def test_super_admin_manages_other_super_admin(self):
        sa1 = _mock_user(UserRole.super_admin, user_id=1)
        sa2 = _mock_user(UserRole.super_admin, user_id=2)
        assert can_manage_user(sa1, sa2)

    def test_admin_manages_lower_roles(self):
        admin = _mock_user(UserRole.admin, user_id=1)
        mod = _mock_user(UserRole.moderator, user_id=2)
        attendee = _mock_user(UserRole.attendee, user_id=3)
        assert can_manage_user(admin, mod)
        assert can_manage_user(admin, attendee)

    def test_admin_cannot_manage_other_admin(self):
        a1 = _mock_user(UserRole.admin, user_id=1)
        a2 = _mock_user(UserRole.admin, user_id=2)
        assert not can_manage_user(a1, a2)

    def test_admin_cannot_manage_super_admin(self):
        admin = _mock_user(UserRole.admin, user_id=1)
        sa = _mock_user(UserRole.super_admin, user_id=2)
        assert not can_manage_user(admin, sa)

    def test_moderator_cannot_manage_admin(self):
        mod = _mock_user(UserRole.moderator, user_id=1)
        admin = _mock_user(UserRole.admin, user_id=2)
        assert not can_manage_user(mod, admin)


# ─── Utilities ────────────────────────────────────────────────────

class TestHashIP:
    def test_deterministic(self):
        assert hash_ip("192.168.1.1") == hash_ip("192.168.1.1")

    def test_different_ips_different_hashes(self):
        assert hash_ip("192.168.1.1") != hash_ip("10.0.0.1")

    def test_truncated_to_16_chars(self):
        assert len(hash_ip("1.2.3.4")) == 16


class TestCSRFToken:
    def test_generate_csrf_token(self):
        token = generate_csrf_token()
        assert len(token) == 64  # 32 bytes hex

    def test_validate_csrf_token_match(self):
        token = generate_csrf_token()
        assert validate_csrf_token(token, token)

    def test_validate_csrf_token_mismatch(self):
        assert not validate_csrf_token("abc", "def")

    def test_validate_csrf_token_empty(self):
        assert not validate_csrf_token("", "abc")
        assert not validate_csrf_token("abc", "")
        assert not validate_csrf_token("", "")


class TestRoleHierarchy:
    def test_super_admin_highest(self):
        assert ROLE_HIERARCHY[UserRole.super_admin] == 100

    def test_attendee_lowest(self):
        assert ROLE_HIERARCHY[UserRole.attendee] == 10

    def test_admin_roles_set(self):
        assert UserRole.super_admin in ADMIN_ROLES
        assert UserRole.admin in ADMIN_ROLES
        assert UserRole.attendee not in ADMIN_ROLES


class TestProductionSecretGuard:
    def teardown_method(self):
        from app.core.config import get_settings
        get_settings.cache_clear()

    def _run(self, monkeypatch, env, key):
        from app.core.config import get_settings
        from app.main import _check_production_secrets
        monkeypatch.setenv("ENVIRONMENT", env)
        monkeypatch.setenv("SECRET_KEY", key)
        get_settings.cache_clear()
        _check_production_secrets()

    def test_dev_allows_default(self, monkeypatch):
        from app.core.config import INSECURE_DEFAULT_SECRET
        self._run(monkeypatch, "development", INSECURE_DEFAULT_SECRET)  # no raise

    def test_production_rejects_default(self, monkeypatch):
        from app.core.config import INSECURE_DEFAULT_SECRET
        with pytest.raises(RuntimeError):
            self._run(monkeypatch, "production", INSECURE_DEFAULT_SECRET)

    def test_production_rejects_short(self, monkeypatch):
        with pytest.raises(RuntimeError):
            self._run(monkeypatch, "production", "tooshort")

    def test_production_allows_strong(self, monkeypatch):
        self._run(monkeypatch, "production", "a" * 48)  # no raise
