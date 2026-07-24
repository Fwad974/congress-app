"""
Tests for the content-moderation system: reporting, the moderator queue,
and resolve (remove / dismiss).
"""
from datetime import datetime, timedelta, timezone

import pytest

import app.api.qa as qa_mod
from app.models.report import ContentReport
from app.models.schedule import ScheduleItem, ScheduleType
from app.models.user import UserRole
from tests.conftest import auth_cookie, make_user


@pytest.fixture(autouse=True)
def _no_qa_cooldown(monkeypatch):
    qa_mod._last_submit.clear()
    monkeypatch.setattr(qa_mod, "_SUBMIT_COOLDOWN", 0.0)
    yield


def _poster_comment(client, db, body="bad comment"):
    sp = make_user(db, email="sp@t.com", role=UserRole.speaker)
    author = make_user(db, email="ca@t.com", role=UserRole.attendee)
    pid = client.post("/api/posters", json={"title": "P", "authors": "x"},
                      cookies=auth_cookie(sp)).json()["id"]
    cid = client.post(f"/api/posters/{pid}/comments", json={"body": body},
                      cookies=auth_cookie(author)).json()["comments"][0]["id"]
    return pid, cid


def _question(client, db, text="bad question"):
    att = make_user(db, email="qa@t.com", role=UserRole.attendee)
    now = datetime.now(timezone.utc)
    it = ScheduleItem(title="S", type=ScheduleType.talk,
                      start_time=now + timedelta(hours=1), end_time=now + timedelta(hours=2))
    db.add(it); db.commit(); db.refresh(it)
    qid = client.post(f"/api/sessions/{it.id}/questions", json={"text": text},
                      cookies=auth_cookie(att)).json()["id"]
    return it.id, qid


class TestReporting:
    def test_report_poster_comment(self, client, db):
        pid, cid = _poster_comment(client, db)
        reporter = make_user(db, email="r@t.com", role=UserRole.attendee)
        r = client.post("/api/moderation/report",
                        json={"content_type": "poster_comment", "content_id": cid,
                              "reason": "spam"}, cookies=auth_cookie(reporter))
        assert r.status_code == 201
        assert db.query(ContentReport).filter(ContentReport.content_id == cid).count() == 1

    def test_duplicate_report_is_noop(self, client, db):
        pid, cid = _poster_comment(client, db)
        reporter = make_user(db, email="r2@t.com", role=UserRole.attendee)
        for _ in range(2):
            client.post("/api/moderation/report",
                        json={"content_type": "poster_comment", "content_id": cid},
                        cookies=auth_cookie(reporter))
        assert db.query(ContentReport).count() == 1

    def test_report_missing_content_404(self, client, attendee):
        assert client.post("/api/moderation/report",
                          json={"content_type": "poster_comment", "content_id": 99999},
                          cookies=auth_cookie(attendee)).status_code == 404

    def test_bad_content_type_422(self, client, attendee):
        assert client.post("/api/moderation/report",
                          json={"content_type": "nope", "content_id": 1},
                          cookies=auth_cookie(attendee)).status_code == 422


class TestQueue:
    def test_attendee_cannot_view_queue(self, client, attendee):
        assert client.get("/api/moderation/reports",
                          cookies=auth_cookie(attendee)).status_code == 403

    def test_queue_groups_reports_by_content(self, client, db):
        pid, cid = _poster_comment(client, db)
        for i in range(3):
            u = make_user(db, email=f"g{i}@t.com", role=UserRole.attendee)
            client.post("/api/moderation/report",
                        json={"content_type": "poster_comment", "content_id": cid,
                              "reason": f"r{i}"}, cookies=auth_cookie(u))
        mod = make_user(db, email="m@t.com", role=UserRole.moderator)
        d = client.get("/api/moderation/reports", cookies=auth_cookie(mod)).json()
        assert d["open_count"] == 1
        assert d["reports"][0]["report_count"] == 3
        assert len(d["reports"][0]["reasons"]) == 3
        assert d["reports"][0]["content_preview"] == "bad comment"

    def test_queue_count(self, client, db):
        pid, cid = _poster_comment(client, db)
        u = make_user(db, email="qc@t.com", role=UserRole.attendee)
        client.post("/api/moderation/report",
                    json={"content_type": "poster_comment", "content_id": cid},
                    cookies=auth_cookie(u))
        mod = make_user(db, email="qm@t.com", role=UserRole.moderator)
        assert client.get("/api/moderation/queue-count", cookies=auth_cookie(mod)).json()["count"] == 1
        # attendees get 0 without error
        assert client.get("/api/moderation/queue-count", cookies=auth_cookie(u)).json()["count"] == 0


class TestResolve:
    def _reported(self, client, db, kind="poster_comment"):
        if kind == "poster_comment":
            pid, cid = _poster_comment(client, db)
        else:
            pid, cid = _question(client, db)
        reporter = make_user(db, email="rr@t.com", role=UserRole.attendee)
        rid = client.post("/api/moderation/report",
                          json={"content_type": kind, "content_id": cid},
                          cookies=auth_cookie(reporter)).json()["id"]
        return cid, rid

    def test_remove_deletes_content_and_closes_reports(self, client, db):
        cid, rid = self._reported(client, db, "poster_comment")
        mod = make_user(db, email="rm@t.com", role=UserRole.moderator)
        r = client.post(f"/api/moderation/reports/{rid}/resolve",
                        json={"action": "remove"}, cookies=auth_cookie(mod)).json()
        assert r["removed"] is True
        from app.models.poster import PosterComment
        assert db.query(PosterComment).filter(PosterComment.id == cid).first() is None
        rep = db.query(ContentReport).first()
        assert rep.status == "resolved" and rep.action_taken == "removed"

    def test_remove_question(self, client, db):
        cid, rid = self._reported(client, db, "question")
        mod = make_user(db, email="rq@t.com", role=UserRole.moderator)
        client.post(f"/api/moderation/reports/{rid}/resolve",
                    json={"action": "remove"}, cookies=auth_cookie(mod))
        from app.models.qa import Question
        assert db.query(Question).filter(Question.id == cid).first() is None

    def test_dismiss_keeps_content(self, client, db):
        cid, rid = self._reported(client, db, "poster_comment")
        mod = make_user(db, email="dm@t.com", role=UserRole.moderator)
        client.post(f"/api/moderation/reports/{rid}/resolve",
                    json={"action": "dismiss"}, cookies=auth_cookie(mod))
        from app.models.poster import PosterComment
        assert db.query(PosterComment).filter(PosterComment.id == cid).first() is not None
        assert db.query(ContentReport).first().status == "dismissed"

    def test_attendee_cannot_resolve(self, client, db):
        cid, rid = self._reported(client, db)
        stranger = make_user(db, email="st@t.com", role=UserRole.attendee)
        assert client.post(f"/api/moderation/reports/{rid}/resolve",
                          json={"action": "remove"},
                          cookies=auth_cookie(stranger)).status_code == 403


class TestModeratorPowers:
    def test_moderator_deletes_any_poster_comment(self, client, db):
        pid, cid = _poster_comment(client, db)
        mod = make_user(db, email="pm@t.com", role=UserRole.moderator)
        r = client.request("DELETE", f"/api/posters/{pid}/comments/{cid}",
                           cookies=auth_cookie(mod))
        assert r.status_code == 200

    def test_moderation_page_gated(self, client, db):
        mod = make_user(db, email="pg@t.com", role=UserRole.moderator)
        att = make_user(db, email="pa@t.com", role=UserRole.attendee)
        assert client.get("/moderation", cookies=auth_cookie(mod)).status_code == 200
        r = client.get("/moderation", cookies=auth_cookie(att), follow_redirects=False)
        assert r.status_code == 302

    def test_nav_shows_moderation_for_moderator(self, client, db):
        mod = make_user(db, email="nv@t.com", role=UserRole.moderator)
        att = make_user(db, email="na@t.com", role=UserRole.attendee)
        assert 'href="/moderation"' in client.get("/home", cookies=auth_cookie(mod)).text
        assert 'href="/moderation"' not in client.get("/home", cookies=auth_cookie(att)).text
