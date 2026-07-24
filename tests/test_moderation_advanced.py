"""
Tests for the advanced content-moderation features: auto-flagging, the
warn → 24h mute → suspend escalation ladder (+ enforcement), and the poster
upload-approval workflow.
"""
from datetime import datetime, timedelta, timezone

from app.core import moderation_filter as mf
from app.core.config import get_settings
from app.models.report import ContentReport
from app.models.moderation import ModerationAction  # noqa: F401 - register table
from app.models.poster import Poster
from app.models.schedule import ScheduleItem, ScheduleType
from app.models.audit_log import AuditLog, AuditAction
from app.models.user import User, UserRole
from tests.conftest import auth_cookie, make_user


def _session(db):
    now = datetime.now(timezone.utc)
    it = ScheduleItem(title="Talk", type=ScheduleType.talk,
                      start_time=now + timedelta(hours=1),
                      end_time=now + timedelta(hours=2))
    db.add(it); db.commit(); db.refresh(it)
    return it


# ─── Auto-flag filter (unit) ─────────────────────────────────────
class TestFilter:
    def test_profanity(self):
        assert "profanity" in mf.scan("you are a total bastard")

    def test_external_link(self):
        assert "external link" in mf.scan("visit https://spam.example")
        assert "external link" in mf.scan("go to deals.shop now")

    def test_spam_phrase_and_caps(self):
        assert "spam" in mf.scan("BUY NOW limited offer")
        assert "spam" in mf.scan("THIS IS ALL SHOUTING TEXT HERE")
        assert "spam" in mf.scan("soooooooo good")

    def test_clean_text(self):
        assert mf.scan("What is the role of iPSCs in cardiac repair?") == []


# ─── Auto-flag integration ───────────────────────────────────────
class TestAutoFlag:
    def test_spammy_question_auto_flagged(self, client, db):
        att = make_user(db, email="af@t.com", role=UserRole.attendee)
        sid = _session(db).id
        client.post(f"/api/sessions/{sid}/questions",
                    json={"text": "FREE MONEY at cheap.example!!!!!!"},
                    cookies=auth_cookie(att))
        rep = db.query(ContentReport).filter_by(content_type="question").first()
        assert rep is not None and rep.source == "auto" and rep.reporter_id is None
        assert "Auto-flagged" in rep.reason

    def test_clean_question_not_flagged(self, client, db):
        att = make_user(db, email="af2@t.com", role=UserRole.attendee)
        sid = _session(db).id
        client.post(f"/api/sessions/{sid}/questions",
                    json={"text": "A normal question about stem cells"},
                    cookies=auth_cookie(att))
        assert db.query(ContentReport).count() == 0

    def test_auto_flag_shows_in_queue(self, client, db):
        mod = make_user(db, email="af3@t.com", role=UserRole.moderator)
        att = make_user(db, email="af3a@t.com", role=UserRole.attendee)
        sid = _session(db).id
        client.post(f"/api/sessions/{sid}/questions",
                    json={"text": "buy now http://x.example"}, cookies=auth_cookie(att))
        d = client.get("/api/moderation/reports", cookies=auth_cookie(mod)).json()
        assert d["open_count"] == 1 and d["reports"][0]["auto_flagged"] is True


# ─── Escalation ladder ───────────────────────────────────────────
class TestEscalation:
    def _mod_and_target(self, db, n):
        return (make_user(db, email=f"m{n}@t.com", role=UserRole.moderator),
                make_user(db, email=f"t{n}@t.com", role=UserRole.attendee))

    def test_ladder_progression(self, client, db):
        mod, target = self._mod_and_target(db, 1)
        st = client.get(f"/api/moderation/users/{target.id}/moderation",
                        cookies=auth_cookie(mod)).json()
        assert st["next_step"] == "warn"
        r1 = client.post(f"/api/moderation/users/{target.id}/escalate",
                         json={"reason": "spam"}, cookies=auth_cookie(mod)).json()
        assert r1["applied"] == "warn" and r1["next_step"] == "mute"
        r2 = client.post(f"/api/moderation/users/{target.id}/escalate",
                         json={"reason": "more spam"}, cookies=auth_cookie(mod)).json()
        assert r2["applied"] == "mute" and r2["muted"] is True and r2["next_step"] == "suspend"
        r3 = client.post(f"/api/moderation/users/{target.id}/escalate",
                         json={"reason": "final"}, cookies=auth_cookie(mod)).json()
        assert r3["applied"] == "suspend" and r3["is_active"] is False and r3["next_step"] is None

    def test_escalate_beyond_suspend_400(self, client, db):
        mod, target = self._mod_and_target(db, 2)
        target.is_active = False
        db.commit()
        assert client.post(f"/api/moderation/users/{target.id}/escalate",
                           json={"reason": "x"}, cookies=auth_cookie(mod)).status_code == 400

    def test_mute_sets_expiry_and_records(self, client, db):
        mod, target = self._mod_and_target(db, 3)
        client.post(f"/api/moderation/users/{target.id}/action",
                    json={"action": "mute", "reason": "24h"}, cookies=auth_cookie(mod))
        db.refresh(target)
        assert target.muted_until is not None
        rec = db.query(ModerationAction).filter_by(user_id=target.id, action="mute").one()
        assert rec.expires_at is not None and rec.reason == "24h"

    def test_reason_required(self, client, db):
        mod, target = self._mod_and_target(db, 4)
        assert client.post(f"/api/moderation/users/{target.id}/escalate",
                           json={"reason": "  "}, cookies=auth_cookie(mod)).status_code == 422

    def test_unmute_and_unsuspend(self, client, db):
        mod, target = self._mod_and_target(db, 5)
        client.post(f"/api/moderation/users/{target.id}/action",
                    json={"action": "suspend", "reason": "bad"}, cookies=auth_cookie(mod))
        r = client.post(f"/api/moderation/users/{target.id}/action",
                        json={"action": "unsuspend", "reason": "appeal ok"},
                        cookies=auth_cookie(mod)).json()
        assert r["is_active"] is True

    def test_actions_audit_logged_with_reason(self, client, db):
        mod, target = self._mod_and_target(db, 6)
        client.post(f"/api/moderation/users/{target.id}/action",
                    json={"action": "warn", "reason": "please stop"}, cookies=auth_cookie(mod))
        log = db.query(AuditLog).filter(AuditLog.action == AuditAction.user_warn).first()
        assert log is not None and "please stop" in log.description

    def test_target_notified(self, client, db):
        from app.models.notification import UserNotification
        mod, target = self._mod_and_target(db, 7)
        client.post(f"/api/moderation/users/{target.id}/action",
                    json={"action": "warn", "reason": "note"}, cookies=auth_cookie(mod))
        n = db.query(UserNotification).filter_by(user_id=target.id, kind="moderation").first()
        assert n is not None


# ─── Escalation RBAC ─────────────────────────────────────────────
class TestEscalationRBAC:
    def test_attendee_cannot_escalate(self, client, db):
        att = make_user(db, email="rb1@t.com", role=UserRole.attendee)
        victim = make_user(db, email="rb1v@t.com", role=UserRole.attendee)
        assert client.post(f"/api/moderation/users/{victim.id}/escalate",
                           json={"reason": "x"}, cookies=auth_cookie(att)).status_code == 403

    def test_moderator_cannot_action_admin(self, client, db):
        mod = make_user(db, email="rb2@t.com", role=UserRole.moderator)
        admin = make_user(db, email="rb2a@t.com", role=UserRole.admin)
        assert client.post(f"/api/moderation/users/{admin.id}/escalate",
                           json={"reason": "x"}, cookies=auth_cookie(mod)).status_code == 403

    def test_moderator_cannot_action_self(self, client, db):
        mod = make_user(db, email="rb3@t.com", role=UserRole.moderator)
        assert client.post(f"/api/moderation/users/{mod.id}/escalate",
                           json={"reason": "x"}, cookies=auth_cookie(mod)).status_code == 403


# ─── Enforcement (mute / suspend) ────────────────────────────────
class TestEnforcement:
    def test_muted_user_cannot_post(self, client, db):
        mod = make_user(db, email="en1@t.com", role=UserRole.moderator)
        att = make_user(db, email="en1a@t.com", role=UserRole.attendee)
        sid = _session(db).id
        client.post(f"/api/moderation/users/{att.id}/action",
                    json={"action": "mute", "reason": "spam"}, cookies=auth_cookie(mod))
        r = client.post(f"/api/sessions/{sid}/questions",
                        json={"text": "hello"}, cookies=auth_cookie(att))
        assert r.status_code == 403

    def test_unmute_restores_posting(self, client, db):
        mod = make_user(db, email="en2@t.com", role=UserRole.moderator)
        att = make_user(db, email="en2a@t.com", role=UserRole.attendee)
        sid = _session(db).id
        client.post(f"/api/moderation/users/{att.id}/action",
                    json={"action": "mute", "reason": "spam"}, cookies=auth_cookie(mod))
        client.post(f"/api/moderation/users/{att.id}/action",
                    json={"action": "unmute", "reason": "ok"}, cookies=auth_cookie(mod))
        r = client.post(f"/api/sessions/{sid}/questions",
                        json={"text": "hello again"}, cookies=auth_cookie(att))
        assert r.status_code == 201

    def test_expired_mute_allows_posting(self, client, db):
        att = make_user(db, email="en3@t.com", role=UserRole.attendee)
        att.muted_until = datetime.now(timezone.utc) - timedelta(hours=1)  # past
        db.commit()
        sid = _session(db).id
        r = client.post(f"/api/sessions/{sid}/questions",
                        json={"text": "still fine"}, cookies=auth_cookie(att))
        assert r.status_code == 201

    def test_suspended_user_rejected_at_auth(self, client, db):
        att = make_user(db, email="en4@t.com", role=UserRole.attendee, is_active=False)
        assert client.get("/api/auth/me", cookies=auth_cookie(att)).status_code == 401
        sid = _session(db).id
        assert client.post(f"/api/sessions/{sid}/questions", json={"text": "x"},
                           cookies=auth_cookie(att)).status_code == 401


# ─── Poster upload-approval workflow ─────────────────────────────
class TestPosterApproval:
    def test_flag_off_auto_approves(self, client, db):
        sp = make_user(db, email="pa0@t.com", role=UserRole.speaker)
        r = client.post("/api/posters",
                        json={"title": "P", "authors": "A", "abstract": "x"},
                        cookies=auth_cookie(sp))
        assert r.json()["status"] == "approved"

    def test_speaker_poster_pending_when_required(self, client, db, monkeypatch):
        monkeypatch.setattr(get_settings(), "POSTER_APPROVAL_REQUIRED", True)
        sp = make_user(db, email="pa1@t.com", role=UserRole.speaker)
        other = make_user(db, email="pa1o@t.com", role=UserRole.attendee)
        pid = client.post("/api/posters",
                          json={"title": "Pending P", "authors": "A", "abstract": "x"},
                          cookies=auth_cookie(sp)).json()["id"]
        assert db.query(Poster).get(pid).status == "pending"
        # Hidden from other attendees, visible to the owner.
        others = client.get("/api/posters", cookies=auth_cookie(other)).json()
        assert all(p["id"] != pid for p in others["posters"])
        mine = client.get("/api/posters", cookies=auth_cookie(sp)).json()
        assert any(p["id"] == pid for p in mine["posters"])
        assert client.get(f"/api/posters/{pid}", cookies=auth_cookie(other)).status_code == 404

    def test_admin_poster_auto_approved_when_required(self, client, db, monkeypatch):
        monkeypatch.setattr(get_settings(), "POSTER_APPROVAL_REQUIRED", True)
        admin = make_user(db, email="pa2@t.com", role=UserRole.admin)
        r = client.post("/api/posters",
                        json={"title": "P", "authors": "A", "abstract": "x"},
                        cookies=auth_cookie(admin))
        assert r.json()["status"] == "approved"

    def test_approve_makes_visible_and_notifies(self, client, db, monkeypatch):
        from app.models.notification import UserNotification
        monkeypatch.setattr(get_settings(), "POSTER_APPROVAL_REQUIRED", True)
        mod = make_user(db, email="pa3m@t.com", role=UserRole.moderator)
        sp = make_user(db, email="pa3@t.com", role=UserRole.speaker)
        other = make_user(db, email="pa3o@t.com", role=UserRole.attendee)
        pid = client.post("/api/posters",
                          json={"title": "P", "authors": "A", "abstract": "x"},
                          cookies=auth_cookie(sp)).json()["id"]
        # In the moderator pending queue.
        pend = client.get("/api/moderation/posters", cookies=auth_cookie(mod)).json()
        assert any(p["id"] == pid for p in pend)
        assert client.post(f"/api/moderation/posters/{pid}/approve", json={},
                           cookies=auth_cookie(mod)).status_code == 200
        assert db.query(Poster).get(pid).status == "approved"
        others = client.get("/api/posters", cookies=auth_cookie(other)).json()
        assert any(p["id"] == pid for p in others["posters"])
        assert db.query(UserNotification).filter_by(user_id=sp.id, kind="poster").count() == 1

    def test_reject_notifies_with_reason(self, client, db, monkeypatch):
        from app.models.notification import UserNotification
        monkeypatch.setattr(get_settings(), "POSTER_APPROVAL_REQUIRED", True)
        mod = make_user(db, email="pa4m@t.com", role=UserRole.moderator)
        sp = make_user(db, email="pa4@t.com", role=UserRole.speaker)
        pid = client.post("/api/posters",
                          json={"title": "P", "authors": "A", "abstract": "x"},
                          cookies=auth_cookie(sp)).json()["id"]
        client.post(f"/api/moderation/posters/{pid}/reject",
                    json={"reason": "Off topic"}, cookies=auth_cookie(mod))
        assert db.query(Poster).get(pid).status == "rejected"
        n = db.query(UserNotification).filter_by(user_id=sp.id, kind="poster").first()
        assert n is not None and "Off topic" in n.body

    def test_pending_count_in_stats(self, client, db, monkeypatch):
        monkeypatch.setattr(get_settings(), "POSTER_APPROVAL_REQUIRED", True)
        mod = make_user(db, email="pa5m@t.com", role=UserRole.moderator)
        sp = make_user(db, email="pa5@t.com", role=UserRole.speaker)
        client.post("/api/posters", json={"title": "P", "authors": "A", "abstract": "x"},
                    cookies=auth_cookie(sp))
        s = client.get("/api/moderation/stats", cookies=auth_cookie(mod)).json()
        assert s["pending_posters"] == 1

    def test_attendee_cannot_approve(self, client, db):
        att = make_user(db, email="pa6@t.com", role=UserRole.attendee)
        assert client.post("/api/moderation/posters/1/approve", json={},
                           cookies=auth_cookie(att)).status_code == 403


class TestResolveReason:
    def test_remove_logs_reason(self, client, db):
        mod = make_user(db, email="rr1@t.com", role=UserRole.moderator)
        att = make_user(db, email="rr1a@t.com", role=UserRole.attendee)
        sid = _session(db).id
        qid = client.post(f"/api/sessions/{sid}/questions", json={"text": "reportable"},
                          cookies=auth_cookie(att)).json()["id"]
        rep_id = client.post("/api/moderation/report",
                             json={"content_type": "question", "content_id": qid,
                                   "reason": "spam"}, cookies=auth_cookie(mod)).json()["id"]
        client.post(f"/api/moderation/reports/{rep_id}/resolve",
                    json={"action": "remove", "reason": "clearly spam"},
                    cookies=auth_cookie(mod))
        log = db.query(AuditLog).filter(AuditLog.action == AuditAction.content_remove).first()
        assert log is not None and "clearly spam" in log.description
