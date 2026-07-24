"""
Tests for the session chair role: per-session assignment, scoped live
moderation (Q&A + polls), and the "My Sessions" view.
"""
from datetime import datetime, timedelta, timezone

import pytest

import app.api.qa as qa_mod
from app.models.schedule import ScheduleItem, ScheduleType
from app.models.user import UserRole
from tests.conftest import auth_cookie, make_user


@pytest.fixture(autouse=True)
def _no_qa_cooldown(monkeypatch):
    qa_mod._last_submit.clear()
    monkeypatch.setattr(qa_mod, "_SUBMIT_COOLDOWN", 0.0)
    yield


def _item(db, title="Session", chair_id=None, speaker_id=None):
    now = datetime.now(timezone.utc)
    it = ScheduleItem(title=title, type=ScheduleType.talk,
                      chair_id=chair_id, speaker_id=speaker_id,
                      start_time=now + timedelta(hours=1), end_time=now + timedelta(hours=2))
    db.add(it); db.commit(); db.refresh(it)
    return it


def _ask(client, user, sid, text="q"):
    return client.post(f"/api/sessions/{sid}/questions", json={"text": text},
                       cookies=auth_cookie(user))


class TestChairAssignment:
    def test_admin_assigns_chair_by_email(self, client, db):
        admin = make_user(db, email="a@t.com", role=UserRole.admin)
        chair = make_user(db, email="chair@t.com", role=UserRole.session_chair,
                          full_name="Chair Person")
        now = datetime.now(timezone.utc)
        r = client.post("/api/schedule", json={
            "title": "Panel", "type": "talk",
            "start_time": (now + timedelta(hours=1)).isoformat(),
            "end_time": (now + timedelta(hours=2)).isoformat(),
            "chair_email": "chair@t.com",
        }, cookies=auth_cookie(admin))
        assert r.status_code == 201
        b = r.json()
        assert b["chair_id"] == chair.id and b["chair_name"] == "Chair Person"

    def test_bad_chair_email_rejected(self, client, db):
        admin = make_user(db, email="a2@t.com", role=UserRole.admin)
        now = datetime.now(timezone.utc)
        r = client.post("/api/schedule", json={
            "title": "P", "type": "talk",
            "start_time": (now + timedelta(hours=1)).isoformat(),
            "end_time": (now + timedelta(hours=2)).isoformat(),
            "chair_email": "nobody@t.com",
        }, cookies=auth_cookie(admin))
        assert r.status_code == 400

    def test_update_and_clear_chair(self, client, db):
        admin = make_user(db, email="a3@t.com", role=UserRole.admin)
        chair = make_user(db, email="c3@t.com", role=UserRole.session_chair)
        it = _item(db, chair_id=chair.id)
        # Clear it with "".
        r = client.put(f"/api/schedule/{it.id}", json={"chair_email": ""},
                       cookies=auth_cookie(admin))
        assert r.status_code == 200 and r.json()["chair_id"] is None

    def test_is_chair_flag_and_my_sessions(self, client, db):
        chair = make_user(db, email="c4@t.com", role=UserRole.session_chair)
        mine = _item(db, title="Mine", chair_id=chair.id)
        _item(db, title="Other")
        r = client.get("/api/schedule?chair=me", cookies=auth_cookie(chair)).json()
        assert r["total"] == 1 and r["items"][0]["title"] == "Mine"
        assert r["items"][0]["is_chair"] is True


class TestScopedModeration:
    def test_chair_moderates_own_session_qa(self, client, db):
        chair = make_user(db, email="mc@t.com", role=UserRole.session_chair)
        att = make_user(db, email="ma@t.com", role=UserRole.attendee)
        it = _item(db, chair_id=chair.id)
        qid = _ask(client, att, it.id).json()["id"]
        r = client.put(f"/api/questions/{qid}/status", json={"status": "answered"},
                       cookies=auth_cookie(chair))
        assert r.status_code == 200
        lst = client.get(f"/api/sessions/{it.id}/questions", cookies=auth_cookie(chair)).json()
        assert lst["can_moderate"] is True

    def test_chair_cannot_moderate_other_session(self, client, db):
        chair = make_user(db, email="mc2@t.com", role=UserRole.session_chair)
        att = make_user(db, email="ma2@t.com", role=UserRole.attendee)
        mine = _item(db, chair_id=chair.id)
        other = _item(db, title="Not mine")
        qid = _ask(client, att, other.id).json()["id"]
        # Not the chair of `other` → cannot moderate it.
        assert client.put(f"/api/questions/{qid}/status", json={"status": "answered"},
                          cookies=auth_cookie(chair)).status_code == 403
        lst = client.get(f"/api/sessions/{other.id}/questions",
                         cookies=auth_cookie(chair)).json()
        assert lst["can_moderate"] is False

    def test_chair_runs_polls_only_for_own_session(self, client, db):
        chair = make_user(db, email="pc@t.com", role=UserRole.session_chair)
        mine = _item(db, chair_id=chair.id)
        other = _item(db, title="Other")
        ok = client.post(f"/api/sessions/{mine.id}/polls",
                         json={"question": "Q?", "type": "single", "options": ["a", "b"]},
                         cookies=auth_cookie(chair))
        assert ok.status_code == 201
        bad = client.post(f"/api/sessions/{other.id}/polls",
                          json={"question": "Q?", "type": "single", "options": ["a", "b"]},
                          cookies=auth_cookie(chair))
        assert bad.status_code == 403

    def test_global_moderator_still_moderates_any(self, client, db):
        mod = make_user(db, email="gm@t.com", role=UserRole.moderator)
        it = _item(db)  # no chair assigned
        r = client.post(f"/api/sessions/{it.id}/polls",
                        json={"question": "Q?", "type": "single", "options": ["a", "b"]},
                        cookies=auth_cookie(mod))
        assert r.status_code == 201

    def test_review_chair_no_longer_moderates_live(self, client, db):
        # review_chair is a papers role — it should not run live sessions.
        rc = make_user(db, email="rc@t.com", role=UserRole.review_chair)
        it = _item(db)
        r = client.post(f"/api/sessions/{it.id}/polls",
                        json={"question": "Q?", "type": "single", "options": ["a", "b"]},
                        cookies=auth_cookie(rc))
        assert r.status_code == 403


class TestSchedulePage:
    def test_my_sessions_button_for_chair(self, client, db):
        chair = make_user(db, email="pg@t.com", role=UserRole.session_chair)
        att = make_user(db, email="pa@t.com", role=UserRole.attendee)
        assert "My Sessions" in client.get("/schedule", cookies=auth_cookie(chair)).text
        assert "My Sessions" not in client.get("/schedule", cookies=auth_cookie(att)).text
