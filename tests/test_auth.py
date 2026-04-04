"""
Tests for authentication endpoints: signup, login, logout, profile, password change.
"""
import pytest
from app.models.user import UserRole
from tests.conftest import make_user, auth_cookie


class TestSignup:
    def test_signup_success(self, client):
        resp = client.post("/api/auth/signup", json={
            "email": "new@test.com",
            "password": "ValidPass1",
            "full_name": "New User",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "new@test.com"
        assert data["full_name"] == "New User"
        assert data["role"] == "attendee"
        assert "access_token" in resp.cookies

    def test_signup_duplicate_email(self, client, db):
        make_user(db, email="dup@test.com")
        resp = client.post("/api/auth/signup", json={
            "email": "dup@test.com",
            "password": "ValidPass1",
            "full_name": "Dup User",
        })
        assert resp.status_code == 400
        assert "already registered" in resp.json()["detail"]

    def test_signup_weak_password_too_short(self, client):
        resp = client.post("/api/auth/signup", json={
            "email": "weak@test.com",
            "password": "Ab1",
            "full_name": "Weak",
        })
        assert resp.status_code == 422

    def test_signup_weak_password_no_uppercase(self, client):
        resp = client.post("/api/auth/signup", json={
            "email": "weak@test.com",
            "password": "lowercase1",
            "full_name": "Weak",
        })
        assert resp.status_code == 422

    def test_signup_weak_password_no_digit(self, client):
        resp = client.post("/api/auth/signup", json={
            "email": "weak@test.com",
            "password": "NoDigitHere",
            "full_name": "Weak",
        })
        assert resp.status_code == 422

    def test_signup_invalid_email(self, client):
        resp = client.post("/api/auth/signup", json={
            "email": "not-an-email",
            "password": "ValidPass1",
            "full_name": "Bad Email",
        })
        assert resp.status_code == 422


class TestLogin:
    def test_login_success(self, client, db):
        make_user(db, email="login@test.com", password="Test1234")
        resp = client.post("/api/auth/login", json={
            "email": "login@test.com",
            "password": "Test1234",
        })
        assert resp.status_code == 200
        assert resp.json()["email"] == "login@test.com"
        assert "access_token" in resp.cookies

    def test_login_wrong_password(self, client, db):
        make_user(db, email="login@test.com", password="Test1234")
        resp = client.post("/api/auth/login", json={
            "email": "login@test.com",
            "password": "WrongPass1",
        })
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client):
        resp = client.post("/api/auth/login", json={
            "email": "ghost@test.com",
            "password": "Test1234",
        })
        assert resp.status_code == 401

    def test_login_suspended_user(self, client, db):
        make_user(db, email="suspended@test.com", password="Test1234", is_active=False)
        resp = client.post("/api/auth/login", json={
            "email": "suspended@test.com",
            "password": "Test1234",
        })
        assert resp.status_code == 403
        assert "suspended" in resp.json()["detail"].lower()

    def test_login_lockout_after_5_failures(self, client, db):
        make_user(db, email="lockme@test.com", password="Test1234")
        for _ in range(5):
            client.post("/api/auth/login", json={
                "email": "lockme@test.com",
                "password": "Wrong1234",
            })
        # 6th attempt should be locked
        resp = client.post("/api/auth/login", json={
            "email": "lockme@test.com",
            "password": "Test1234",
        })
        assert resp.status_code == 429
        assert "too many" in resp.json()["detail"].lower() or "attempts" in resp.json()["detail"].lower()

    def test_login_permanent_lockout_after_10_failures(self, client, db):
        """AUTH-03: 10+ failures in 30 min → permanent lock (contact admin)."""
        from app.core.admin_security import login_tracker as lt
        email = "permlock@test.com"
        make_user(db, email=email, password="Test1234")
        # Directly record 10 failures to bypass the 15-min lock at 5
        for _ in range(10):
            lt.record_failure(email)
        resp = client.post("/api/auth/login", json={
            "email": email,
            "password": "Test1234",
        })
        assert resp.status_code == 429
        assert "contact" in resp.json()["detail"].lower()


class TestLogout:
    def test_logout_post(self, client):
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
        assert "logged out" in resp.json()["message"].lower()

    def test_logout_get_redirects(self, client):
        resp = client.get("/api/auth/logout", follow_redirects=False)
        assert resp.status_code == 302


class TestMe:
    def test_me_authenticated(self, client, attendee):
        resp = client.get("/api/auth/me", cookies=auth_cookie(attendee))
        assert resp.status_code == 200
        assert resp.json()["email"] == attendee.email

    def test_me_unauthenticated(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401


class TestProfile:
    def test_update_profile(self, client, db, attendee):
        resp = client.put("/api/auth/profile", json={
            "full_name": "Updated Name",
            "bio": "New bio",
        }, cookies=auth_cookie(attendee))
        assert resp.status_code == 200
        assert resp.json()["full_name"] == "Updated Name"

    def test_update_profile_unauthenticated(self, client):
        resp = client.put("/api/auth/profile", json={"full_name": "Hacker"})
        assert resp.status_code == 401


class TestPasswordChange:
    def test_change_password_success(self, client, db, attendee):
        resp = client.put("/api/auth/password", json={
            "current_password": "Test1234",
            "new_password": "NewPass123",
        }, cookies=auth_cookie(attendee))
        assert resp.status_code == 200

        # Verify new password works
        resp2 = client.post("/api/auth/login", json={
            "email": attendee.email,
            "password": "NewPass123",
        })
        assert resp2.status_code == 200

    def test_change_password_wrong_current(self, client, attendee):
        resp = client.put("/api/auth/password", json={
            "current_password": "WrongCurrent1",
            "new_password": "NewPass123",
        }, cookies=auth_cookie(attendee))
        assert resp.status_code == 400


class TestDeleteAccount:
    def test_delete_account(self, client, db, attendee):
        resp = client.delete("/api/auth/account", cookies=auth_cookie(attendee))
        assert resp.status_code == 200
        assert "deleted" in resp.json()["message"].lower()
