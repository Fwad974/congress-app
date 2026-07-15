"""
Tests for OAuth social login: the find-or-create/link logic, the provider
gating, and that OAuth-only accounts can't password-login.

The redirect handshake itself needs real provider credentials, so it's not
exercised here — we test the pieces around it.
"""
from app.core import oauth as oauth_mod
from app.core.oauth import upsert_oauth_user, enabled_providers
from app.models.user import User, UserRole
from app.models.oauth import OAuthAccount
from tests.conftest import auth_cookie, make_user


class TestUpsert:
    def test_creates_new_google_user(self, db):
        u = upsert_oauth_user(db, "google", "g-1", "alice@x.com", "Alice",
                              email_verified=True)
        assert u.email == "alice@x.com"
        assert u.role == UserRole.attendee
        assert u.is_verified is True
        assert u.hashed_password is None
        assert db.query(OAuthAccount).filter_by(
            provider="google", provider_user_id="g-1").count() == 1

    def test_same_sub_returns_same_user(self, db):
        u1 = upsert_oauth_user(db, "google", "g-2", "bob@x.com", "Bob", True)
        u2 = upsert_oauth_user(db, "google", "g-2", "bob@x.com", "Bob", True)
        assert u1.id == u2.id
        assert db.query(OAuthAccount).filter_by(provider="google").count() == 1

    def test_links_to_existing_verified_email(self, db):
        existing = make_user(db, email="carol@x.com", full_name="Carol")
        pw = existing.hashed_password
        u = upsert_oauth_user(db, "google", "g-3", "carol@x.com", "Carol", True)
        assert u.id == existing.id
        assert u.hashed_password == pw            # password login preserved
        assert db.query(OAuthAccount).filter_by(
            provider="google", provider_user_id="g-3").count() == 1

    def test_unverified_email_does_not_link(self, db):
        existing = make_user(db, email="dave@x.com", full_name="Dave")
        u = upsert_oauth_user(db, "google", "g-4", "dave@x.com", "Dave",
                              email_verified=False)
        assert u.id != existing.id                # separate account
        assert u.email != "dave@x.com"            # de-duplicated placeholder

    def test_orcid_without_email(self, db):
        u = upsert_oauth_user(db, "orcid", "0000-0002-1234-5678", None, "Dr O",
                              email_verified=False)
        assert u.email.endswith("@orcid.local")
        assert u.orcid_id == "0000-0002-1234-5678"


class TestProviderGating:
    def test_none_enabled_by_default(self, client):
        r = client.get("/api/auth/oauth/providers")
        assert r.status_code == 200
        assert r.json()["providers"] == []

    def test_login_and_callback_404_when_disabled(self, client):
        assert client.get("/api/auth/oauth/google/login",
                         follow_redirects=False).status_code == 404
        assert client.get("/api/auth/oauth/google/callback",
                         follow_redirects=False).status_code == 404

    def test_enabled_providers_reflects_config(self, monkeypatch):
        monkeypatch.setattr(oauth_mod.settings, "GOOGLE_CLIENT_ID", "cid")
        monkeypatch.setattr(oauth_mod.settings, "GOOGLE_CLIENT_SECRET", "sec")
        assert "google" in enabled_providers()
        assert "orcid" not in enabled_providers()

    def test_login_page_hides_buttons_when_disabled(self, client):
        r = client.get("/login")
        assert r.status_code == 200
        assert "/api/auth/oauth/google/login" not in r.text


class TestPasswordGuard:
    def test_oauth_only_user_cannot_password_login(self, client, db):
        upsert_oauth_user(db, "google", "g-5", "erin@x.com", "Erin", True)
        r = client.post("/api/auth/login",
                        json={"email": "erin@x.com", "password": "whatever"})
        assert r.status_code == 401

    def test_password_user_still_logs_in(self, client, db):
        make_user(db, email="frank@x.com", password="Test1234")
        r = client.post("/api/auth/login",
                        json={"email": "frank@x.com", "password": "Test1234"})
        assert r.status_code == 200
