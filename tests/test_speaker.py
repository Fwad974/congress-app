"""
Tests for speaker (presenter) features on Program & Schedule:
presenter linkage, "My Talks", presenter self-service editing, and
speaker moderation of their own session's Q&A.
"""
from datetime import datetime, timedelta, timezone

import pytest

import app.api.qa as qa_mod
from app.models.schedule import ScheduleItem
from app.models.qa import Question, QuestionStatus
from app.models.user import UserRole
from tests.conftest import auth_cookie, make_user


@pytest.fixture(autouse=True)
def _no_qa_cooldown(monkeypatch):
    """The Q&A submit cooldown is a module global; reset it per test so the
    reused test-DB ids don't collide across tests."""
    qa_mod._last_submit.clear()
    monkeypatch.setattr(qa_mod, "_SUBMIT_COOLDOWN", 0.0)
    yield


def _times():
    now = datetime.now(timezone.utc)
    return (now + timedelta(hours=2)).isoformat(), (now + timedelta(hours=3)).isoformat()


def _create(client, admin, **over):
    s, e = _times()
    body = {"title": "Talk", "type": "talk", "start_time": s, "end_time": e}
    body.update(over)
    return client.post("/api/schedule", json=body, cookies=auth_cookie(admin))


class TestPresenterAssignment:
    def test_assign_presenter_by_email(self, client, admin_user, db):
        spk = make_user(db, email="spk@test.com", role=UserRole.speaker, full_name="Dr Spk")
        r = _create(client, admin_user, speaker_email="spk@test.com")
        assert r.status_code == 201
        body = r.json()
        assert body["speaker_id"] == spk.id
        assert body["speaker_email"] == "spk@test.com"
        assert body["speaker_name"] == "Dr Spk"

    def test_unknown_email_rejected(self, client, admin_user):
        assert _create(client, admin_user, speaker_email="ghost@test.com").status_code == 400

    def test_is_presenter_flag_for_speaker(self, client, admin_user, db):
        spk = make_user(db, email="s2@test.com", role=UserRole.speaker)
        iid = _create(client, admin_user, speaker_email="s2@test.com").json()["id"]
        # The speaker sees is_presenter=True; a random attendee sees False.
        mine = client.get(f"/api/schedule/{iid}", cookies=auth_cookie(spk)).json()
        assert mine["is_presenter"] is True
        other = make_user(db, email="a2@test.com", role=UserRole.attendee)
        theirs = client.get(f"/api/schedule/{iid}", cookies=auth_cookie(other)).json()
        assert theirs["is_presenter"] is False

    def test_my_talks_filter(self, client, admin_user, db):
        spk = make_user(db, email="s3@test.com", role=UserRole.speaker)
        _create(client, admin_user, speaker_email="s3@test.com", title="Mine")
        _create(client, admin_user, title="NotMine")
        d = client.get("/api/schedule?presenter=me", cookies=auth_cookie(spk)).json()
        assert [i["title"] for i in d["items"]] == ["Mine"]

    def test_clear_presenter(self, client, admin_user, db):
        make_user(db, email="s4@test.com", role=UserRole.speaker)
        iid = _create(client, admin_user, speaker_email="s4@test.com").json()["id"]
        r = client.put(f"/api/schedule/{iid}", json={"speaker_email": ""},
                       cookies=auth_cookie(admin_user))
        assert r.status_code == 200 and r.json()["speaker_id"] is None


class TestPresenterSelfService:
    def _talk(self, client, admin_user, db, email="pres@test.com"):
        make_user(db, email=email, role=UserRole.speaker)
        return _create(client, admin_user, speaker_email=email).json()["id"]

    def test_presenter_edits_abstract_and_slides(self, client, admin_user, db):
        spk = make_user(db, email="p1@test.com", role=UserRole.speaker)
        iid = _create(client, admin_user, speaker_email="p1@test.com").json()["id"]
        r = client.put(f"/api/schedule/{iid}/presentation", json={
            "abstract": "Our new findings…",
            "materials_url": "https://example.com/slides.pdf",
            "description": "Updated by speaker",
        }, cookies=auth_cookie(spk))
        assert r.status_code == 200
        body = r.json()
        assert body["extra"]["abstract"] == "Our new findings…"
        assert body["extra"]["materials_url"] == "https://example.com/slides.pdf"
        assert body["description"] == "Updated by speaker"

    def test_non_presenter_forbidden(self, client, admin_user, db):
        make_user(db, email="p2@test.com", role=UserRole.speaker)
        iid = _create(client, admin_user, speaker_email="p2@test.com").json()["id"]
        intruder = make_user(db, email="x@test.com", role=UserRole.attendee)
        r = client.put(f"/api/schedule/{iid}/presentation", json={"abstract": "hax"},
                       cookies=auth_cookie(intruder))
        assert r.status_code == 403

    def test_admin_can_edit_presentation(self, client, admin_user, db):
        make_user(db, email="p3@test.com", role=UserRole.speaker)
        iid = _create(client, admin_user, speaker_email="p3@test.com").json()["id"]
        r = client.put(f"/api/schedule/{iid}/presentation", json={"abstract": "by admin"},
                       cookies=auth_cookie(admin_user))
        assert r.status_code == 200

    def test_bad_materials_url_rejected(self, client, admin_user, db):
        spk = make_user(db, email="p4@test.com", role=UserRole.speaker)
        iid = _create(client, admin_user, speaker_email="p4@test.com").json()["id"]
        r = client.put(f"/api/schedule/{iid}/presentation", json={"materials_url": "javascript:evil"},
                       cookies=auth_cookie(spk))
        assert r.status_code == 422


class TestSpeakerQaModeration:
    def test_speaker_moderates_own_session(self, client, admin_user, attendee, db):
        spk = make_user(db, email="qspk@test.com", role=UserRole.speaker)
        iid = _create(client, admin_user, speaker_email="qspk@test.com").json()["id"]
        qid = client.post(f"/api/sessions/{iid}/questions", json={"text": "q?"},
                          cookies=auth_cookie(attendee)).json()["id"]
        # can_moderate is reported true to the presenter
        lst = client.get(f"/api/sessions/{iid}/questions", cookies=auth_cookie(spk)).json()
        assert lst["can_moderate"] is True
        # and they can actually mark answered
        r = client.put(f"/api/questions/{qid}/status", json={"status": "answered"},
                       cookies=auth_cookie(spk))
        assert r.status_code == 200 and r.json()["status"] == "answered"

    def test_speaker_of_other_session_cannot(self, client, admin_user, attendee, db):
        spk_a = make_user(db, email="qa@test.com", role=UserRole.speaker)
        make_user(db, email="qb@test.com", role=UserRole.speaker)
        iid_a = _create(client, admin_user, speaker_email="qa@test.com").json()["id"]
        iid_b = _create(client, admin_user, speaker_email="qb@test.com").json()["id"]
        qid_b = client.post(f"/api/sessions/{iid_b}/questions", json={"text": "q?"},
                            cookies=auth_cookie(attendee)).json()["id"]
        # Speaker of session A can't moderate session B's question.
        r = client.put(f"/api/questions/{qid_b}/status", json={"status": "hidden"},
                       cookies=auth_cookie(spk_a))
        assert r.status_code == 403
