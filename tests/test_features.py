"""
Tests for the admin feature-release (feature-flag) dashboard.
"""
from app.models.feature_flag import FeatureFlag  # noqa: F401 - register table
from app.models.poster import Poster  # noqa: F401 - register for /api/posters guard
from app.models.audit_log import AuditLog, AuditAction
from app.models.user import UserRole
from tests.conftest import auth_cookie, make_user


def _admin(db, email="fadmin@test.com", role=UserRole.admin):
    return make_user(db, email=email, role=role)


class TestFeatureAdminAPI:
    def test_attendee_cannot_list(self, client, attendee):
        assert client.get("/api/admin/features",
                          cookies=auth_cookie(attendee)).status_code == 403

    def test_admin_lists_all_enabled_by_default(self, client, db):
        admin = _admin(db)
        d = client.get("/api/admin/features", cookies=auth_cookie(admin)).json()
        keys = {f["key"] for f in d["features"]}
        assert {"papers", "posters", "notes", "qa", "polls"} <= keys
        assert all(f["enabled"] for f in d["features"])

    def test_toggle_and_audit(self, client, db):
        admin = _admin(db)
        r = client.put("/api/admin/features/papers", json={"enabled": False},
                       cookies=auth_cookie(admin))
        assert r.status_code == 200 and r.json()["enabled"] is False
        d = client.get("/api/admin/features", cookies=auth_cookie(admin)).json()
        papers = next(f for f in d["features"] if f["key"] == "papers")
        assert papers["enabled"] is False
        assert db.query(AuditLog).filter(
            AuditLog.action == AuditAction.feature_toggle).count() >= 1

    def test_unknown_feature_404(self, client, db):
        admin = _admin(db)
        assert client.put("/api/admin/features/nope", json={"enabled": False},
                          cookies=auth_cookie(admin)).status_code == 404


class TestFeatureEnforcement:
    def test_disabled_hides_api_for_attendee_not_admin(self, client, db):
        admin = _admin(db, email="fa2@test.com", role=UserRole.super_admin)
        att = make_user(db, email="fu2@test.com", role=UserRole.attendee)
        client.put("/api/admin/features/posters", json={"enabled": False},
                   cookies=auth_cookie(admin))
        # Attendee is blocked from the posters API; admin can still use it.
        assert client.get("/api/posters", cookies=auth_cookie(att)).status_code == 403
        assert client.get("/api/posters", cookies=auth_cookie(admin)).status_code == 200

    def test_disabled_page_redirects_attendee(self, client, db):
        admin = _admin(db, email="fa3@test.com")
        att = make_user(db, email="fu3@test.com", role=UserRole.attendee)
        client.put("/api/admin/features/papers", json={"enabled": False},
                   cookies=auth_cookie(admin))
        r = client.get("/papers", cookies=auth_cookie(att), follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"].endswith("/home")
        # Admin can still preview.
        assert client.get("/papers", cookies=auth_cookie(admin)).status_code == 200

    def test_nav_link_hidden_when_disabled(self, client, db):
        admin = _admin(db, email="fa4@test.com")
        att = make_user(db, email="fu4@test.com", role=UserRole.attendee)
        # Enabled → link present.
        assert 'href="/posters"' in client.get("/home", cookies=auth_cookie(att)).text
        client.put("/api/admin/features/posters", json={"enabled": False},
                   cookies=auth_cookie(admin))
        assert 'href="/posters"' not in client.get("/home", cookies=auth_cookie(att)).text

    def test_reenable_restores_access(self, client, db):
        admin = _admin(db, email="fa5@test.com")
        att = make_user(db, email="fu5@test.com", role=UserRole.attendee)
        client.put("/api/admin/features/posters", json={"enabled": False},
                   cookies=auth_cookie(admin))
        assert client.get("/api/posters", cookies=auth_cookie(att)).status_code == 403
        client.put("/api/admin/features/posters", json={"enabled": True},
                   cookies=auth_cookie(admin))
        assert client.get("/api/posters", cookies=auth_cookie(att)).status_code == 200


class TestReleasesTab:
    def test_tab_present_for_admin(self, client, db):
        admin = _admin(db, email="fa6@test.com")
        assert 'data-tab="releases"' in client.get("/admin", cookies=auth_cookie(admin)).text
