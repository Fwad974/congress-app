"""
Tests for live polls (REST layer).
"""
from datetime import datetime, timedelta, timezone

from app.models.schedule import ScheduleItem, ScheduleType
from app.models.poll import Poll, PollResponse
from tests.conftest import auth_cookie, make_user
from app.models.user import UserRole


def _item(db, title="Session"):
    now = datetime.now(timezone.utc)
    item = ScheduleItem(title=title, type=ScheduleType.talk,
                        start_time=now + timedelta(hours=1),
                        end_time=now + timedelta(hours=2))
    db.add(item); db.commit(); db.refresh(item)
    return item


def _create(client, chair, sid, question="Pick one", ptype="single",
            options=("A", "B", "C")):
    return client.post(f"/api/sessions/{sid}/polls",
                       json={"question": question, "type": ptype, "options": list(options)},
                       cookies=auth_cookie(chair))


def _open(client, chair, poll_id):
    return client.put(f"/api/polls/{poll_id}/status", json={"status": "open"},
                      cookies=auth_cookie(chair))


class TestPollCreation:
    def test_unauthenticated_blocked(self, client, db):
        item = _item(db)
        assert client.get(f"/api/sessions/{item.id}/polls").status_code == 401

    def test_attendee_cannot_create(self, client, attendee, db):
        item = _item(db)
        assert _create(client, attendee, item.id).status_code == 403

    def test_chair_creates_draft(self, client, moderator, db):
        item = _item(db)
        r = _create(client, moderator, item.id)
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "draft"
        assert [o["text"] for o in body["options"]] == ["A", "B", "C"]

    def test_choice_needs_two_options(self, client, moderator, db):
        item = _item(db)
        assert _create(client, moderator, item.id, options=("only",)).status_code == 400

    def test_attendee_does_not_see_drafts(self, client, moderator, attendee, db):
        item = _item(db)
        _create(client, moderator, item.id)
        lst = client.get(f"/api/sessions/{item.id}/polls",
                         cookies=auth_cookie(attendee)).json()
        assert lst["polls"] == []
        assert lst["can_moderate"] is False


class TestVoting:
    def test_cannot_vote_until_open(self, client, moderator, attendee, db):
        item = _item(db)
        pid = _create(client, moderator, item.id).json()["id"]
        opt = client.get(f"/api/polls/{pid}", cookies=auth_cookie(moderator)).json()["options"][0]["id"]
        r = client.post(f"/api/polls/{pid}/vote", json={"option_ids": [opt]},
                        cookies=auth_cookie(attendee))
        assert r.status_code == 400

    def test_single_vote_and_replace(self, client, moderator, attendee, db):
        item = _item(db)
        pid = _create(client, moderator, item.id).json()["id"]
        _open(client, moderator, pid)
        opts = client.get(f"/api/polls/{pid}", cookies=auth_cookie(attendee)).json()["options"]
        a, b = opts[0]["id"], opts[1]["id"]

        r1 = client.post(f"/api/polls/{pid}/vote", json={"option_ids": [a]},
                         cookies=auth_cookie(attendee)).json()
        assert r1["my_option_ids"] == [a]
        counts = {o["id"]: o["count"] for o in r1["options"]}
        assert counts[a] == 1

        # Re-vote replaces, doesn't add.
        r2 = client.post(f"/api/polls/{pid}/vote", json={"option_ids": [b]},
                         cookies=auth_cookie(attendee)).json()
        counts = {o["id"]: o["count"] for o in r2["options"]}
        assert counts[a] == 0 and counts[b] == 1

    def test_single_requires_exactly_one(self, client, moderator, attendee, db):
        item = _item(db)
        pid = _create(client, moderator, item.id).json()["id"]
        _open(client, moderator, pid)
        assert client.post(f"/api/polls/{pid}/vote", json={"option_ids": []},
                          cookies=auth_cookie(attendee)).status_code == 400

    def test_multiple_choice(self, client, moderator, attendee, db):
        item = _item(db)
        pid = _create(client, moderator, item.id, ptype="multiple").json()["id"]
        _open(client, moderator, pid)
        opts = client.get(f"/api/polls/{pid}", cookies=auth_cookie(attendee)).json()["options"]
        a, b = opts[0]["id"], opts[1]["id"]
        r = client.post(f"/api/polls/{pid}/vote", json={"option_ids": [a, b]},
                        cookies=auth_cookie(attendee)).json()
        assert set(r["my_option_ids"]) == {a, b}

    def test_two_users_counted(self, client, moderator, attendee, db):
        item = _item(db)
        pid = _create(client, moderator, item.id).json()["id"]
        _open(client, moderator, pid)
        other = make_user(db, email="v2@test.com", role=UserRole.attendee)
        a = client.get(f"/api/polls/{pid}", cookies=auth_cookie(attendee)).json()["options"][0]["id"]
        client.post(f"/api/polls/{pid}/vote", json={"option_ids": [a]}, cookies=auth_cookie(attendee))
        r = client.post(f"/api/polls/{pid}/vote", json={"option_ids": [a]}, cookies=auth_cookie(other)).json()
        counts = {o["id"]: o["count"] for o in r["options"]}
        assert counts[a] == 2


class TestWordCloud:
    def test_submit_and_aggregate(self, client, moderator, attendee, db):
        item = _item(db)
        pid = _create(client, moderator, item.id, ptype="wordcloud", options=()).json()["id"]
        _open(client, moderator, pid)
        other = make_user(db, email="w2@test.com", role=UserRole.attendee)
        client.post(f"/api/polls/{pid}/vote", json={"text": "Innovation"}, cookies=auth_cookie(attendee))
        client.post(f"/api/polls/{pid}/vote", json={"text": "innovation "}, cookies=auth_cookie(other))
        client.post(f"/api/polls/{pid}/vote", json={"text": "science"}, cookies=auth_cookie(other))
        res = client.get(f"/api/polls/{pid}/results", cookies=auth_cookie(moderator)).json()
        words = {w["text"]: w["count"] for w in res["words"]}
        assert words["innovation"] == 2   # normalized + merged
        assert words["science"] == 1
        assert res["total"] == 3

    def test_empty_word_rejected(self, client, moderator, attendee, db):
        item = _item(db)
        pid = _create(client, moderator, item.id, ptype="wordcloud", options=()).json()["id"]
        _open(client, moderator, pid)
        assert client.post(f"/api/polls/{pid}/vote", json={"text": "   "},
                          cookies=auth_cookie(attendee)).status_code == 400

    def test_per_user_cap(self, client, moderator, attendee, db):
        item = _item(db)
        pid = _create(client, moderator, item.id, ptype="wordcloud", options=()).json()["id"]
        _open(client, moderator, pid)
        for i in range(5):
            assert client.post(f"/api/polls/{pid}/vote", json={"text": f"w{i}"},
                              cookies=auth_cookie(attendee)).status_code == 200
        assert client.post(f"/api/polls/{pid}/vote", json={"text": "toomany"},
                          cookies=auth_cookie(attendee)).status_code == 429


class TestLifecycle:
    def test_attendee_cannot_open_or_delete(self, client, moderator, attendee, db):
        item = _item(db)
        pid = _create(client, moderator, item.id).json()["id"]
        assert client.put(f"/api/polls/{pid}/status", json={"status": "open"},
                         cookies=auth_cookie(attendee)).status_code == 403
        assert client.delete(f"/api/polls/{pid}",
                            cookies=auth_cookie(attendee)).status_code == 403

    def test_open_makes_visible_then_delete(self, client, moderator, attendee, db):
        item = _item(db)
        pid = _create(client, moderator, item.id).json()["id"]
        _open(client, moderator, pid)
        lst = client.get(f"/api/sessions/{item.id}/polls",
                         cookies=auth_cookie(attendee)).json()
        assert len(lst["polls"]) == 1 and lst["polls"][0]["status"] == "open"
        assert client.delete(f"/api/polls/{pid}",
                            cookies=auth_cookie(moderator)).status_code == 200
        assert db.query(Poll).count() == 0


class TestPresentPage:
    def test_requires_login(self, client):
        assert client.get("/present/1", follow_redirects=False).status_code == 302

    def test_renders(self, client, attendee, db):
        item = _item(db)
        r = client.get(f"/present/{item.id}", cookies=auth_cookie(attendee))
        assert r.status_code == 200
