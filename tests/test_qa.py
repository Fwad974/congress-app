"""
Tests for live Q&A (REST layer) and the realtime broadcaster.

The SSE stream itself isn't exercised over HTTP (it's a long-lived stream);
instead the broadcaster's in-process pub/sub is unit-tested directly.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

import app.api.qa as qa_mod
from app.models.schedule import ScheduleItem, ScheduleType
from app.models.qa import Question, QuestionStatus
from app.models.audit_log import AuditLog, AuditAction
from app.models.user import UserRole
from tests.conftest import auth_cookie, make_user


@pytest.fixture(autouse=True)
def _reset_qa(monkeypatch):
    """No submit cooldown during tests (except where explicitly tested)."""
    qa_mod._last_submit.clear()
    monkeypatch.setattr(qa_mod, "_SUBMIT_COOLDOWN", 0.0)
    yield
    qa_mod._last_submit.clear()


def _item(db, title="Keynote"):
    now = datetime.now(timezone.utc)
    item = ScheduleItem(
        title=title, type=ScheduleType.talk,
        start_time=now + timedelta(hours=1), end_time=now + timedelta(hours=2),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _ask(client, user, session_id, text):
    return client.post(f"/api/sessions/{session_id}/questions",
                       json={"text": text}, cookies=auth_cookie(user))


class TestBroadcaster:
    def test_in_process_pub_sub(self):
        from app.core.realtime import Broadcaster

        async def go():
            b = Broadcaster()  # not connected → in-process mode
            async with b.subscribe("rt:qa:7") as q:
                await b.publish("rt:qa:7", {"type": "created", "x": 1})
                return await asyncio.wait_for(q.get(), timeout=1)

        raw = asyncio.run(go())
        assert json.loads(raw)["x"] == 1


class TestQuestions:
    def test_unauthenticated_blocked(self, client, db):
        item = _item(db)
        assert client.get(f"/api/sessions/{item.id}/questions").status_code == 401
        assert client.post(f"/api/sessions/{item.id}/questions",
                          json={"text": "hi"}).status_code == 401

    def test_create_and_list(self, client, attendee, db):
        item = _item(db)
        r = _ask(client, attendee, item.id, "  What about dosing?  ")
        assert r.status_code == 201
        body = r.json()
        assert body["text"] == "What about dosing?"
        assert body["upvotes"] == 0
        assert body["is_mine"] is True

        lst = client.get(f"/api/sessions/{item.id}/questions",
                         cookies=auth_cookie(attendee)).json()
        assert lst["total"] == 1
        assert lst["can_moderate"] is False

    def test_create_on_missing_session(self, client, attendee):
        assert _ask(client, attendee, 99999, "hi").status_code == 404

    def test_empty_rejected(self, client, attendee, db):
        item = _item(db)
        assert _ask(client, attendee, item.id, "   ").status_code == 422

    def test_upvote_is_idempotent_and_counted(self, client, attendee, db):
        item = _item(db)
        qid = _ask(client, attendee, item.id, "q").json()["id"]
        other = make_user(db, email="o@test.com", role=UserRole.attendee)

        r1 = client.post(f"/api/questions/{qid}/upvote", cookies=auth_cookie(attendee))
        assert r1.json()["upvotes"] == 1 and r1.json()["has_upvoted"] is True
        # Same user upvoting again does not double-count.
        r2 = client.post(f"/api/questions/{qid}/upvote", cookies=auth_cookie(attendee))
        assert r2.json()["upvotes"] == 1
        # A different user adds one.
        r3 = client.post(f"/api/questions/{qid}/upvote", cookies=auth_cookie(other))
        assert r3.json()["upvotes"] == 2

        # Remove one.
        r4 = client.delete(f"/api/questions/{qid}/upvote", cookies=auth_cookie(attendee))
        assert r4.json()["upvotes"] == 1 and r4.json()["has_upvoted"] is False

    def test_attendee_cannot_moderate(self, client, attendee, db):
        item = _item(db)
        qid = _ask(client, attendee, item.id, "q").json()["id"]
        r = client.put(f"/api/questions/{qid}/status", json={"status": "answered"},
                       cookies=auth_cookie(attendee))
        assert r.status_code == 403

    def test_moderator_marks_answered(self, client, attendee, moderator, db):
        item = _item(db)
        qid = _ask(client, attendee, item.id, "q").json()["id"]
        r = client.put(f"/api/questions/{qid}/status", json={"status": "answered"},
                       cookies=auth_cookie(moderator))
        assert r.status_code == 200
        assert r.json()["status"] == "answered"
        lst = client.get(f"/api/sessions/{item.id}/questions",
                         cookies=auth_cookie(moderator)).json()
        assert lst["can_moderate"] is True

    def test_hidden_invisible_to_attendees(self, client, attendee, moderator, db):
        item = _item(db)
        qid = _ask(client, attendee, item.id, "secret").json()["id"]
        client.put(f"/api/questions/{qid}/status", json={"status": "hidden"},
                   cookies=auth_cookie(moderator))
        # Attendee can't see it...
        a = client.get(f"/api/sessions/{item.id}/questions",
                       cookies=auth_cookie(attendee)).json()
        assert a["total"] == 0
        # ...but a moderator can.
        m = client.get(f"/api/sessions/{item.id}/questions",
                       cookies=auth_cookie(moderator)).json()
        assert m["total"] == 1
        # And it can't be upvoted while hidden.
        assert client.post(f"/api/questions/{qid}/upvote",
                          cookies=auth_cookie(attendee)).status_code == 404

    def test_author_deletes_own(self, client, attendee, db):
        item = _item(db)
        qid = _ask(client, attendee, item.id, "mine").json()["id"]
        assert client.delete(f"/api/questions/{qid}",
                            cookies=auth_cookie(attendee)).status_code == 200
        assert db.query(Question).count() == 0

    def test_non_owner_cannot_delete(self, client, attendee, db):
        item = _item(db)
        qid = _ask(client, attendee, item.id, "mine").json()["id"]
        other = make_user(db, email="o2@test.com", role=UserRole.attendee)
        assert client.delete(f"/api/questions/{qid}",
                            cookies=auth_cookie(other)).status_code == 403

    def test_moderator_deletes_and_logs(self, client, attendee, moderator, db):
        item = _item(db)
        qid = _ask(client, attendee, item.id, "remove me").json()["id"]
        assert client.delete(f"/api/questions/{qid}",
                            cookies=auth_cookie(moderator)).status_code == 200
        log = db.query(AuditLog).filter(
            AuditLog.action == AuditAction.question_moderate).first()
        assert log is not None and log.target_type == "question"

    def test_submit_cooldown(self, client, attendee, db, monkeypatch):
        item = _item(db)
        monkeypatch.setattr(qa_mod, "_SUBMIT_COOLDOWN", 60.0)
        qa_mod._last_submit.clear()
        assert _ask(client, attendee, item.id, "one").status_code == 201
        assert _ask(client, attendee, item.id, "two").status_code == 429


class TestQaPage:
    def test_requires_login(self, client):
        assert client.get("/qa/1", follow_redirects=False).status_code == 302

    def test_renders(self, client, attendee, db):
        item = _item(db)
        r = client.get(f"/qa/{item.id}", cookies=auth_cookie(attendee))
        assert r.status_code == 200
        assert "Live" in r.text
