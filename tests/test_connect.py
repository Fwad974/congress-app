"""
Tests for the opt-in networking directory.
"""
from app.models.user import UserRole
from tests.conftest import auth_cookie, make_user


class TestVisibility:
    def test_hidden_by_default(self, client, db, attendee):
        d = client.get("/api/connect/directory", cookies=auth_cookie(attendee)).json()
        assert d["total"] == 0 and d["visible"] is False

    def test_opt_in_lists_me(self, client, db, attendee):
        d = client.put("/api/connect/visibility", json={"visible": True},
                       cookies=auth_cookie(attendee)).json()
        assert d["visible"] is True and d["total"] == 1
        assert d["people"][0]["is_me"] is True

    def test_opt_out_removes_me(self, client, db, attendee):
        client.put("/api/connect/visibility", json={"visible": True},
                   cookies=auth_cookie(attendee))
        d = client.put("/api/connect/visibility", json={"visible": False},
                       cookies=auth_cookie(attendee)).json()
        assert d["visible"] is False and d["total"] == 0

    def test_only_opted_in_are_listed(self, client, db):
        visible = make_user(db, email="v1@test.com", full_name="Visible One",
                            networking_visible=True, institution="MIT")
        make_user(db, email="h1@test.com", full_name="Hidden One")
        viewer = make_user(db, email="vw@test.com")
        d = client.get("/api/connect/directory", cookies=auth_cookie(viewer)).json()
        names = [p["full_name"] for p in d["people"]]
        assert names == ["Visible One"]
        assert d["people"][0]["email"] == "v1@test.com"


class TestSearch:
    def test_search_by_name_and_institution(self, client, db):
        make_user(db, email="s1@test.com", full_name="Alice Stem",
                  networking_visible=True, institution="Harvard")
        make_user(db, email="s2@test.com", full_name="Bob Cell",
                  networking_visible=True, institution="Yale")
        viewer = make_user(db, email="s3@test.com")
        d = client.get("/api/connect/directory?search=harvard",
                       cookies=auth_cookie(viewer)).json()
        assert [p["full_name"] for p in d["people"]] == ["Alice Stem"]


class TestGating:
    def test_flag_off_blocks_api_and_page(self, client, db):
        admin = make_user(db, email="ga@test.com", role=UserRole.admin)
        att = make_user(db, email="gu@test.com", role=UserRole.attendee)
        client.put("/api/admin/features/connect", json={"enabled": False},
                   cookies=auth_cookie(admin))
        assert client.get("/api/connect/directory",
                         cookies=auth_cookie(att)).status_code == 403
        r = client.get("/connect", cookies=auth_cookie(att), follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"].endswith("/home")

    def test_nav_shows_connect_when_on(self, client, attendee):
        assert 'href="/connect"' in client.get("/home", cookies=auth_cookie(attendee)).text


class TestPage:
    def test_requires_login(self, client):
        assert client.get("/connect", follow_redirects=False).status_code == 302

    def test_renders(self, client, attendee):
        r = client.get("/connect", cookies=auth_cookie(attendee))
        assert r.status_code == 200 and "Directory visibility" in r.text
