"""
Tests for the Poster Gallery: browsing, voting, comments, scavenger hunt.
"""
from app.models.poster import Poster, PosterVote, PosterComment, PosterVisit  # noqa: F401
from app.models.user import UserRole
from tests.conftest import auth_cookie, make_user

POST = {"title": "Neural organoids", "authors": "A. Author, B. Writer",
        "category": "iPSC", "abstract": "We grew brains."}


def _speaker(db, email="sp@test.com"):
    return make_user(db, email=email, role=UserRole.speaker)


def _create(client, user, **over):
    body = dict(POST); body.update(over)
    return client.post("/api/posters", json=body, cookies=auth_cookie(user))


class TestCreate:
    def test_attendee_cannot_create(self, client, attendee):
        assert _create(client, attendee).status_code == 403

    def test_speaker_creates_with_hunt_code(self, client, db):
        sp = _speaker(db)
        r = _create(client, sp)
        assert r.status_code == 201
        b = r.json()
        assert b["is_mine"] is True and b["can_edit"] is True
        assert b["hunt_code"] and len(b["hunt_code"]) >= 6

    def test_empty_title_rejected(self, client, db):
        assert _create(client, _speaker(db), title="  ").status_code == 422

    def test_hunt_codes_unique(self, client, db):
        sp = _speaker(db)
        codes = {_create(client, sp).json()["hunt_code"] for _ in range(5)}
        assert len(codes) == 5


class TestGalleryAndVoting:
    def test_list_and_vote_toggle(self, client, db):
        sp = _speaker(db)
        pid = _create(client, sp).json()["id"]
        att = make_user(db, email="a1@test.com", role=UserRole.attendee)
        # vote
        r = client.post(f"/api/posters/{pid}/vote", cookies=auth_cookie(att)).json()
        assert r["vote_count"] == 1 and r["my_vote"] is True
        # idempotent
        r = client.post(f"/api/posters/{pid}/vote", cookies=auth_cookie(att)).json()
        assert r["vote_count"] == 1
        # unvote
        r = client.request("DELETE", f"/api/posters/{pid}/vote",
                           cookies=auth_cookie(att)).json()
        assert r["vote_count"] == 0 and r["my_vote"] is False

    def test_sort_by_votes(self, client, db):
        sp = _speaker(db)
        a = _create(client, sp, title="Low").json()["id"]
        b = _create(client, sp, title="High").json()["id"]
        voter = make_user(db, email="v@test.com", role=UserRole.attendee)
        client.post(f"/api/posters/{b}/vote", cookies=auth_cookie(voter))
        posters = client.get("/api/posters?sort=votes", cookies=auth_cookie(sp)).json()["posters"]
        assert posters[0]["title"] == "High"

    def test_hunt_code_hidden_from_non_owner(self, client, db):
        sp = _speaker(db)
        pid = _create(client, sp).json()["id"]
        att = make_user(db, email="a2@test.com", role=UserRole.attendee)
        seen = client.get(f"/api/posters/{pid}", cookies=auth_cookie(att)).json()
        assert seen["hunt_code"] is None


class TestComments:
    def _poster(self, client, db):
        sp = _speaker(db, email="spc@test.com")
        return sp, _create(client, sp).json()["id"]

    def test_add_and_count(self, client, db):
        sp, pid = self._poster(client, db)
        att = make_user(db, email="ac@test.com", role=UserRole.attendee)
        r = client.post(f"/api/posters/{pid}/comments", json={"body": "Great work!"},
                        cookies=auth_cookie(att)).json()
        assert r["comment_count"] == 1
        assert r["comments"][0]["body"] == "Great work!" and r["comments"][0]["is_mine"] is True

    def test_author_can_delete_own(self, client, db):
        sp, pid = self._poster(client, db)
        att = make_user(db, email="ac2@test.com", role=UserRole.attendee)
        cid = client.post(f"/api/posters/{pid}/comments", json={"body": "x"},
                          cookies=auth_cookie(att)).json()["comments"][0]["id"]
        r = client.request("DELETE", f"/api/posters/{pid}/comments/{cid}",
                           cookies=auth_cookie(att))
        assert r.status_code == 200 and r.json()["comment_count"] == 0

    def test_stranger_cannot_delete(self, client, db):
        sp, pid = self._poster(client, db)
        att = make_user(db, email="ac3@test.com", role=UserRole.attendee)
        other = make_user(db, email="ac4@test.com", role=UserRole.attendee)
        cid = client.post(f"/api/posters/{pid}/comments", json={"body": "x"},
                          cookies=auth_cookie(att)).json()["comments"][0]["id"]
        assert client.request("DELETE", f"/api/posters/{pid}/comments/{cid}",
                              cookies=auth_cookie(other)).status_code == 403

    def test_poster_owner_can_moderate(self, client, db):
        sp, pid = self._poster(client, db)
        att = make_user(db, email="ac5@test.com", role=UserRole.attendee)
        cid = client.post(f"/api/posters/{pid}/comments", json={"body": "x"},
                          cookies=auth_cookie(att)).json()["comments"][0]["id"]
        assert client.request("DELETE", f"/api/posters/{pid}/comments/{cid}",
                              cookies=auth_cookie(sp)).status_code == 200


class TestScavengerHunt:
    def test_checkin_by_code_and_progress(self, client, db):
        sp = _speaker(db, email="sph2@test.com")
        p = _create(client, sp).json()
        att = make_user(db, email="ah2@test.com", role=UserRole.attendee)
        h = client.post("/api/posters/hunt", json={"code": p["hunt_code"]},
                        cookies=auth_cookie(att)).json()
        assert h["visited"] == 1 and p["id"] in h["visited_poster_ids"]

    def test_bad_code(self, client, db):
        att = make_user(db, email="ah3@test.com", role=UserRole.attendee)
        assert client.post("/api/posters/hunt", json={"code": "NOPE1234"},
                          cookies=auth_cookie(att)).status_code == 404

    def test_no_id_based_visit_endpoint(self, client, db):
        # The bypassable id-based /visit endpoint must not exist — the hunt
        # requires the secret code (scanned QR or typed), not just a poster id.
        sp = _speaker(db, email="sph4@test.com")
        pid = _create(client, sp).json()["id"]
        att = make_user(db, email="ah4@test.com", role=UserRole.attendee)
        assert client.post(f"/api/posters/{pid}/visit",
                          cookies=auth_cookie(att)).status_code in (404, 405)

    def test_checkin_idempotent(self, client, db):
        sp = _speaker(db, email="sph4b@test.com")
        code = _create(client, sp).json()["hunt_code"]
        att = make_user(db, email="ah4b@test.com", role=UserRole.attendee)
        client.post("/api/posters/hunt", json={"code": code}, cookies=auth_cookie(att))
        client.post("/api/posters/hunt", json={"code": code}, cookies=auth_cookie(att))
        assert client.get("/api/posters/hunt", cookies=auth_cookie(att)).json()["visited"] == 1

    def test_hunt_completes_at_goal(self, client, db):
        from app.core.config import get_settings
        goal = get_settings().POSTER_HUNT_GOAL
        sp = _speaker(db, email="sph5@test.com")
        att = make_user(db, email="ah5@test.com", role=UserRole.attendee)
        for i in range(goal):
            code = _create(client, sp, title=f"P{i}").json()["hunt_code"]
            client.post("/api/posters/hunt", json={"code": code}, cookies=auth_cookie(att))
        h = client.get("/api/posters/hunt", cookies=auth_cookie(att)).json()
        assert h["complete"] is True and h["visited"] >= goal


class TestEditDelete:
    def test_owner_edits(self, client, db):
        sp = _speaker(db, email="se@test.com")
        pid = _create(client, sp).json()["id"]
        r = client.put(f"/api/posters/{pid}", json={"title": "Renamed"},
                       cookies=auth_cookie(sp))
        assert r.status_code == 200 and r.json()["title"] == "Renamed"

    def test_other_speaker_cannot_edit(self, client, db):
        sp = _speaker(db, email="se2@test.com")
        pid = _create(client, sp).json()["id"]
        sp2 = _speaker(db, email="se3@test.com")
        assert client.put(f"/api/posters/{pid}", json={"title": "Hijack"},
                          cookies=auth_cookie(sp2)).status_code == 403

    def test_admin_can_delete_any(self, client, db):
        sp = _speaker(db, email="se4@test.com")
        pid = _create(client, sp).json()["id"]
        admin = make_user(db, email="adm@test.com", role=UserRole.super_admin)
        assert client.request("DELETE", f"/api/posters/{pid}",
                              cookies=auth_cookie(admin)).status_code == 204
        assert client.get(f"/api/posters/{pid}", cookies=auth_cookie(admin)).status_code == 404


class TestImage:
    def test_upload_and_serve(self, client, db):
        sp = _speaker(db, email="si@test.com")
        pid = _create(client, sp).json()["id"]
        r = client.post(f"/api/posters/{pid}/image",
                        files={"file": ("p.png", b"\x89PNG\r\n\x1a\n data", "image/png")},
                        cookies=auth_cookie(sp))
        assert r.status_code == 200 and r.json()["has_image"] is True
        att = make_user(db, email="ai@test.com", role=UserRole.attendee)
        d = client.get(f"/api/posters/{pid}/image", cookies=auth_cookie(att))
        assert d.status_code == 200 and b"PNG" in d.content

    def test_bad_extension_rejected(self, client, db):
        sp = _speaker(db, email="si2@test.com")
        pid = _create(client, sp).json()["id"]
        r = client.post(f"/api/posters/{pid}/image",
                        files={"file": ("evil.exe", b"MZ", "application/octet-stream")},
                        cookies=auth_cookie(sp))
        assert r.status_code == 422


class TestHuntQR:
    def test_owner_gets_svg(self, client, db):
        sp = _speaker(db, email="qr1@test.com")
        pid = _create(client, sp).json()["id"]
        r = client.get(f"/api/posters/{pid}/qr", cookies=auth_cookie(sp))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/svg+xml")
        assert b"<svg" in r.content

    def test_stranger_cannot_get_qr(self, client, db):
        sp = _speaker(db, email="qr2@test.com")
        pid = _create(client, sp).json()["id"]
        att = make_user(db, email="qr2a@test.com", role=UserRole.attendee)
        assert client.get(f"/api/posters/{pid}/qr",
                         cookies=auth_cookie(att)).status_code == 403

    def test_print_page_owner_only(self, client, db):
        sp = _speaker(db, email="qr3@test.com")
        p = _create(client, sp).json()
        r = client.get(f"/posters/{p['id']}/qr-print", cookies=auth_cookie(sp))
        assert r.status_code == 200 and p["hunt_code"] in r.text
        att = make_user(db, email="qr3a@test.com", role=UserRole.attendee)
        r2 = client.get(f"/posters/{p['id']}/qr-print", cookies=auth_cookie(att),
                        follow_redirects=False)
        assert r2.status_code == 302


class TestPostersPage:
    def test_requires_login(self, client):
        assert client.get("/posters", follow_redirects=False).status_code == 302

    def test_renders(self, client, attendee):
        r = client.get("/posters", cookies=auth_cookie(attendee))
        assert r.status_code == 200 and "Poster" in r.text

    def test_add_button_only_for_creators(self, client, db):
        sp = _speaker(db, email="sr@test.com")
        att = make_user(db, email="ar@test.com", role=UserRole.attendee)
        assert "Add poster" in client.get("/posters", cookies=auth_cookie(sp)).text
        assert "Add poster" not in client.get("/posters", cookies=auth_cookie(att)).text
