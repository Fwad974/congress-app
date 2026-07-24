"""
Tests for Smart Notifications additions: quiet hours, targeted broadcasts,
emergency alerts, and quiet-hours-aware web push.
"""
from datetime import datetime, timezone

from app.core.notification_service import in_quiet_hours
from app.models.notification import (
    UserNotification, Broadcast, NotificationSettings, PushSubscription,
)
from app.models.user import UserRole
from tests.conftest import auth_cookie, make_user

BC = "/api/admin/notifications/broadcast"


class TestQuietHoursHelper:
    def test_disabled_is_false(self):
        now = datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)
        assert in_quiet_hours({"quiet_hours": {"enabled": False}}, now) is False

    def test_no_tz_fails_open(self):
        now = datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)
        prefs = {"quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00"}}
        assert in_quiet_hours(prefs, now) is False  # can't localize → deliver

    def test_overnight_window_inside(self):
        now = datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)  # 02:00 UTC
        prefs = {"tz": "UTC", "quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00"}}
        assert in_quiet_hours(prefs, now) is True

    def test_overnight_window_outside(self):
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        prefs = {"tz": "UTC", "quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00"}}
        assert in_quiet_hours(prefs, now) is False

    def test_same_day_window(self):
        now = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
        prefs = {"tz": "UTC", "quiet_hours": {"enabled": True, "start": "12:00", "end": "14:00"}}
        assert in_quiet_hours(prefs, now) is True


class TestPrefs:
    def test_quiet_hours_round_trip(self, client, attendee):
        r = client.put("/api/notifications/settings", json={
            "enabled": True, "lead_times": [10], "sound": "bell", "volume": 0.5,
            "quiet_hours": {"enabled": True, "start": "23:00", "end": "06:30"},
            "tz": "Asia/Dubai",
        }, cookies=auth_cookie(attendee))
        assert r.status_code == 200
        p = r.json()["prefs"]
        assert p["quiet_hours"] == {"enabled": True, "start": "23:00", "end": "06:30"}
        assert p["tz"] == "Asia/Dubai"

    def test_invalid_quiet_time_rejected(self, client, attendee):
        r = client.put("/api/notifications/settings", json={
            "enabled": True, "lead_times": [10], "sound": "bell", "volume": 0.5,
            "quiet_hours": {"enabled": True, "start": "25:00", "end": "06:00"},
        }, cookies=auth_cookie(attendee))
        assert r.status_code == 422


class TestBroadcast:
    def test_attendee_cannot_broadcast(self, client, attendee):
        r = client.post(BC, json={"title": "x", "body": "y"}, cookies=auth_cookie(attendee))
        assert r.status_code == 403

    def test_admin_broadcast_to_all_excludes_actor(self, client, admin_user, attendee, db):
        r = client.post(BC, json={"title": "Hello", "body": "World"},
                        cookies=auth_cookie(admin_user))
        assert r.status_code == 200
        assert r.json()["recipient_count"] >= 1
        n = db.query(UserNotification).filter(
            UserNotification.user_id == attendee.id).first()
        assert n and n.kind == "announcement" and n.title == "Hello"
        # The sender doesn't notify themselves.
        assert db.query(UserNotification).filter(
            UserNotification.user_id == admin_user.id).count() == 0

    def test_target_roles(self, client, admin_user, attendee, db):
        spk = make_user(db, email="spk@test.com", role=UserRole.speaker)
        client.post(BC, json={"title": "T", "body": "B", "target_roles": ["speaker"]},
                    cookies=auth_cookie(admin_user))
        assert db.query(UserNotification).filter(
            UserNotification.user_id == spk.id).count() == 1
        assert db.query(UserNotification).filter(
            UserNotification.user_id == attendee.id).count() == 0

    def test_emergency_kind(self, client, admin_user, attendee, db):
        client.post(BC, json={"title": "E", "body": "B", "emergency": True},
                    cookies=auth_cookie(admin_user))
        n = db.query(UserNotification).filter(
            UserNotification.user_id == attendee.id).first()
        assert n.kind == "emergency"

    def test_daily_limit_and_emergency_exempt(self, client, admin_user, attendee, db):
        for i in range(3):
            assert client.post(BC, json={"title": f"t{i}", "body": "b"},
                              cookies=auth_cookie(admin_user)).status_code == 200
        # 4th non-emergency is blocked...
        assert client.post(BC, json={"title": "t4", "body": "b"},
                          cookies=auth_cookie(admin_user)).status_code == 429
        # ...but emergency still goes out.
        assert client.post(BC, json={"title": "E", "body": "b", "emergency": True},
                          cookies=auth_cookie(admin_user)).status_code == 200

    def test_broadcast_list(self, client, admin_user, attendee):
        client.post(BC, json={"title": "Listed", "body": "b"}, cookies=auth_cookie(admin_user))
        d = client.get("/api/admin/notifications/broadcasts",
                       cookies=auth_cookie(admin_user)).json()
        assert d["daily_limit"] == 3 and d["sent_today"] >= 1
        assert any(b["title"] == "Listed" for b in d["broadcasts"])


class TestTargetedAudience:
    def _rejected_author(self, client, db):
        from app.models.paper import Paper, PaperStatus
        author = make_user(db, email="rej@test.com", role=UserRole.attendee)
        db.add(Paper(author_id=author.id, title="T", authors="X", abstract="ab",
                     status=PaperStatus.rejected))
        db.commit()
        return author

    def test_preview_requires_admin(self, client, attendee):
        assert client.post(BC + "/preview", json={"audience": {}},
                          cookies=auth_cookie(attendee)).status_code == 403

    def test_preview_counts_and_sample(self, client, admin_user, attendee, db):
        author = self._rejected_author(client, db)
        r = client.post(BC + "/preview", json={"audience": {"paper_status": ["rejected"]}},
                        cookies=auth_cookie(admin_user)).json()
        assert r["count"] == 1 and r["summary"] == "paper rejected"
        assert r["sample"][0]["email"] == "rej@test.com"

    def test_send_to_paper_status(self, client, admin_user, attendee, db):
        author = self._rejected_author(client, db)
        client.post(BC, json={"title": "Revise", "body": "See reviews",
                              "audience": {"paper_status": ["rejected"]}},
                    cookies=auth_cookie(admin_user))
        assert db.query(UserNotification).filter(
            UserNotification.user_id == author.id).count() == 1
        # A plain attendee (no rejected paper) is not notified.
        assert db.query(UserNotification).filter(
            UserNotification.user_id == attendee.id).count() == 0

    def test_specific_people_only(self, client, admin_user, db):
        a = make_user(db, email="p1@test.com", role=UserRole.attendee)
        b = make_user(db, email="p2@test.com", role=UserRole.attendee)
        client.post(BC, json={"title": "Hi", "body": "you",
                              "audience": {"user_ids": [a.id]}},
                    cookies=auth_cookie(admin_user))
        assert db.query(UserNotification).filter(UserNotification.user_id == a.id).count() == 1
        assert db.query(UserNotification).filter(UserNotification.user_id == b.id).count() == 0

    def test_roles_and_people_union(self, client, admin_user, db):
        spk = make_user(db, email="us1@test.com", role=UserRole.speaker)
        att = make_user(db, email="us2@test.com", role=UserRole.attendee)
        client.post(BC, json={"title": "T", "body": "b",
                              "audience": {"roles": ["speaker"], "user_ids": [att.id]}},
                    cookies=auth_cookie(admin_user))
        assert db.query(UserNotification).filter(UserNotification.user_id == spk.id).count() == 1
        assert db.query(UserNotification).filter(UserNotification.user_id == att.id).count() == 1

    def test_registered_after_filter(self, client, admin_user, db):
        # Everyone here was just created, so a far-future cutoff yields nobody.
        r = client.post(BC + "/preview",
                        json={"audience": {"registered_after": "2999-01-01"}},
                        cookies=auth_cookie(admin_user)).json()
        assert r["count"] == 0

    def test_empty_audience_rejected_when_no_match(self, client, admin_user, db):
        # user_ids referencing nobody real → no recipients → 400.
        r = client.post(BC, json={"title": "T", "body": "b",
                                  "audience": {"user_ids": [999999]}},
                        cookies=auth_cookie(admin_user))
        assert r.status_code == 400

    def test_history_shows_audience_summary(self, client, admin_user, db):
        self._rejected_author(client, db)
        client.post(BC, json={"title": "Seg", "body": "b",
                              "audience": {"paper_status": ["rejected"]}},
                    cookies=auth_cookie(admin_user))
        d = client.get("/api/admin/notifications/broadcasts",
                       cookies=auth_cookie(admin_user)).json()
        row = next(b for b in d["broadcasts"] if b["title"] == "Seg")
        assert row["audience_summary"] == "paper rejected"


class TestPushQuietHours:
    def _enable(self, monkeypatch):
        from app.core import push_service
        monkeypatch.setattr(push_service.settings, "VAPID_PUBLIC_KEY", "p")
        monkeypatch.setattr(push_service.settings, "VAPID_PRIVATE_KEY", "p")
        return push_service

    def test_default_user_delivers(self, monkeypatch, db, attendee):
        ps = self._enable(monkeypatch)
        monkeypatch.setattr(ps, "_webpush", lambda **kw: None)
        db.add(PushSubscription(user_id=attendee.id, endpoint="https://p/1",
                                p256dh="p", auth="a"))
        db.commit()
        assert ps.send_push_to_users(db, [attendee.id], {"title": "T"}) == 1

    def test_quiet_hours_skips_but_emergency_bypasses(self, monkeypatch, db, attendee):
        ps = self._enable(monkeypatch)
        calls = []
        monkeypatch.setattr(ps, "_webpush", lambda **kw: calls.append(kw))
        db.add(PushSubscription(user_id=attendee.id, endpoint="https://p/2",
                                p256dh="p", auth="a"))
        db.add(NotificationSettings(user_id=attendee.id, prefs={
            "enabled": True, "tz": "UTC",
            "quiet_hours": {"enabled": True, "start": "00:00", "end": "23:59"},
        }))
        db.commit()
        assert ps.send_push_to_users(db, [attendee.id], {"title": "T"}) == 0
        assert ps.send_push_to_users(db, [attendee.id], {"title": "T"}, emergency=True) == 1

    def test_disabled_user_skipped(self, monkeypatch, db, attendee):
        ps = self._enable(monkeypatch)
        monkeypatch.setattr(ps, "_webpush", lambda **kw: None)
        db.add(PushSubscription(user_id=attendee.id, endpoint="https://p/3",
                                p256dh="p", auth="a"))
        db.add(NotificationSettings(user_id=attendee.id, prefs={"enabled": False}))
        db.commit()
        assert ps.send_push_to_users(db, [attendee.id], {"title": "T"}) == 0
