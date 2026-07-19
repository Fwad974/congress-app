"""
Tests for the notification preferences and upcoming-bookmarks API.
"""
from datetime import datetime, timedelta, timezone

from app.models.schedule import ScheduleItem, ScheduleType, ScheduleBookmark
from app.models.notification import (
    NotificationSettings, UserNotification, PushSubscription,
)
from app.schemas.notification import DEFAULT_PREFS
from tests.conftest import auth_cookie, make_user
from app.models.user import UserRole


class TestNotificationSettings:
    def test_unauthenticated_blocked(self, client):
        assert client.get("/api/notifications/settings").status_code == 401
        assert client.put("/api/notifications/settings",
                          json={"enabled": True, "lead_times": [10],
                                "sound": "bell", "volume": 0.5}).status_code == 401

    def test_get_returns_defaults_for_new_user(self, client, attendee):
        resp = client.get("/api/notifications/settings",
                          cookies=auth_cookie(attendee))
        assert resp.status_code == 200
        prefs = resp.json()["prefs"]
        assert prefs["enabled"] == DEFAULT_PREFS["enabled"]
        assert sorted(prefs["lead_times"]) == sorted(DEFAULT_PREFS["lead_times"])
        assert prefs["sound"] == DEFAULT_PREFS["sound"]

    def test_update_round_trip(self, client, attendee):
        resp = client.put(
            "/api/notifications/settings",
            json={"enabled": False, "lead_times": [5, 15],
                  "sound": "chime", "volume": 0.4},
            cookies=auth_cookie(attendee),
        )
        assert resp.status_code == 200
        prefs = resp.json()["prefs"]
        assert prefs["enabled"] is False
        assert prefs["lead_times"] == [15, 5]   # sorted desc
        assert prefs["sound"] == "chime"
        assert abs(prefs["volume"] - 0.4) < 1e-6

        # Persists across calls
        again = client.get("/api/notifications/settings",
                           cookies=auth_cookie(attendee)).json()
        assert again["prefs"]["sound"] == "chime"

    def test_invalid_lead_times_filtered(self, client, attendee):
        resp = client.put(
            "/api/notifications/settings",
            json={"enabled": True, "lead_times": [10, 999, 7, 0],
                  "sound": "bell", "volume": 0.7},
            cookies=auth_cookie(attendee),
        )
        assert resp.status_code == 200
        assert resp.json()["prefs"]["lead_times"] == [10, 0]

    def test_invalid_sound_rejected(self, client, attendee):
        resp = client.put(
            "/api/notifications/settings",
            json={"enabled": True, "lead_times": [10],
                  "sound": "siren", "volume": 0.5},
            cookies=auth_cookie(attendee),
        )
        assert resp.status_code == 422

    def test_volume_clamped(self, client, attendee):
        resp = client.put(
            "/api/notifications/settings",
            json={"enabled": True, "lead_times": [10],
                  "sound": "bell", "volume": 5.0},
            cookies=auth_cookie(attendee),
        )
        assert resp.status_code == 200
        assert resp.json()["prefs"]["volume"] == 1.0


class TestUpcoming:
    def test_unauthenticated_blocked(self, client):
        assert client.get("/api/notifications/upcoming").status_code == 401

    def test_returns_only_bookmarked_future_items(self, client, attendee, db):
        now = datetime.now(timezone.utc)
        future = ScheduleItem(
            title="Future bookmarked", type=ScheduleType.talk,
            start_time=now + timedelta(hours=2),
            end_time=now + timedelta(hours=3),
        )
        not_bookmarked = ScheduleItem(
            title="Not bookmarked", type=ScheduleType.talk,
            start_time=now + timedelta(hours=2),
            end_time=now + timedelta(hours=3),
        )
        past = ScheduleItem(
            title="Already ended", type=ScheduleType.talk,
            start_time=now - timedelta(hours=2),
            end_time=now - timedelta(hours=1),
        )
        db.add_all([future, not_bookmarked, past])
        db.flush()
        db.add(ScheduleBookmark(user_id=attendee.id, schedule_item_id=future.id))
        db.add(ScheduleBookmark(user_id=attendee.id, schedule_item_id=past.id))
        db.commit()

        resp = client.get("/api/notifications/upcoming",
                          cookies=auth_cookie(attendee))
        assert resp.status_code == 200
        body = resp.json()
        titles = [i["title"] for i in body["items"]]
        assert titles == ["Future bookmarked"]
        assert "prefs" in body
        assert "server_time" in body


def _make_item(db, **kw):
    now = datetime.now(timezone.utc)
    defaults = dict(
        title="Session", type=ScheduleType.talk,
        start_time=now + timedelta(hours=2),
        end_time=now + timedelta(hours=3),
    )
    defaults.update(kw)
    item = ScheduleItem(**defaults)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


class TestScheduleChangeNotifications:
    def test_update_notifies_bookmarkers_only(self, client, attendee, admin_user, db):
        other = make_user(db, email="other@test.com", role=UserRole.attendee)
        item = _make_item(db, title="Keynote")
        db.add(ScheduleBookmark(user_id=attendee.id, schedule_item_id=item.id))
        db.commit()

        new_start = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        new_end = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
        resp = client.put(
            f"/api/schedule/{item.id}",
            json={"start_time": new_start, "end_time": new_end},
            cookies=auth_cookie(admin_user),
        )
        assert resp.status_code == 200

        # Bookmarker gets a notification; non-bookmarker does not.
        assert db.query(UserNotification).filter(
            UserNotification.user_id == attendee.id).count() == 1
        assert db.query(UserNotification).filter(
            UserNotification.user_id == other.id).count() == 0

        n = db.query(UserNotification).filter(
            UserNotification.user_id == attendee.id).first()
        assert n.kind == "updated"
        assert n.title == "Keynote"
        assert "rescheduled" in n.body.lower()

    def test_admin_editor_not_notified_for_own_bookmark(self, client, admin_user, db):
        item = _make_item(db, title="Panel", location="Hall A")
        db.add(ScheduleBookmark(user_id=admin_user.id, schedule_item_id=item.id))
        db.commit()

        resp = client.put(
            f"/api/schedule/{item.id}",
            json={"location": "Hall B"},
            cookies=auth_cookie(admin_user),
        )
        assert resp.status_code == 200
        assert db.query(UserNotification).filter(
            UserNotification.user_id == admin_user.id).count() == 0

    def test_no_op_update_creates_no_notification(self, client, attendee, admin_user, db):
        item = _make_item(db, title="Workshop")
        db.add(ScheduleBookmark(user_id=attendee.id, schedule_item_id=item.id))
        db.commit()

        resp = client.put(
            f"/api/schedule/{item.id}",
            json={"title": "Workshop"},  # unchanged
            cookies=auth_cookie(admin_user),
        )
        assert resp.status_code == 200
        assert db.query(UserNotification).filter(
            UserNotification.user_id == attendee.id).count() == 0

    def test_delete_notifies_cancellation(self, client, attendee, admin_user, db):
        item = _make_item(db, title="Doomed talk")
        db.add(ScheduleBookmark(user_id=attendee.id, schedule_item_id=item.id))
        db.commit()

        resp = client.delete(f"/api/schedule/{item.id}",
                             cookies=auth_cookie(admin_user))
        assert resp.status_code == 200

        n = db.query(UserNotification).filter(
            UserNotification.user_id == attendee.id).first()
        assert n is not None
        assert n.kind == "cancelled"
        assert n.title == "Doomed talk"
        assert "cancelled" in n.body.lower()


class TestFeed:
    def test_unauthenticated_blocked(self, client):
        assert client.get("/api/notifications/feed").status_code == 401

    def test_feed_lists_and_counts_unread(self, client, attendee, db):
        db.add_all([
            UserNotification(user_id=attendee.id, kind="updated",
                             title="A", body="changed"),
            UserNotification(user_id=attendee.id, kind="cancelled",
                             title="B", body="gone"),
        ])
        db.commit()

        resp = client.get("/api/notifications/feed",
                          cookies=auth_cookie(attendee))
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["unread_count"] == 2
        assert all(i["read"] is False for i in body["items"])

    def test_feed_is_per_user(self, client, attendee, db):
        other = make_user(db, email="other2@test.com", role=UserRole.attendee)
        db.add(UserNotification(user_id=other.id, kind="updated",
                                title="Theirs", body="x"))
        db.commit()
        resp = client.get("/api/notifications/feed",
                          cookies=auth_cookie(attendee))
        assert resp.json()["items"] == []

    def test_mark_one_read(self, client, attendee, db):
        n = UserNotification(user_id=attendee.id, kind="updated",
                             title="A", body="changed")
        db.add(n)
        db.commit()
        db.refresh(n)

        resp = client.post(f"/api/notifications/feed/{n.id}/read",
                           cookies=auth_cookie(attendee))
        assert resp.status_code == 200
        feed = client.get("/api/notifications/feed",
                         cookies=auth_cookie(attendee)).json()
        assert feed["unread_count"] == 0
        assert feed["items"][0]["read"] is True

    def test_mark_read_rejects_other_users_notification(self, client, attendee, db):
        other = make_user(db, email="other3@test.com", role=UserRole.attendee)
        n = UserNotification(user_id=other.id, kind="updated",
                             title="A", body="x")
        db.add(n)
        db.commit()
        db.refresh(n)
        resp = client.post(f"/api/notifications/feed/{n.id}/read",
                           cookies=auth_cookie(attendee))
        assert resp.status_code == 404

    def test_mark_all_read(self, client, attendee, db):
        db.add_all([
            UserNotification(user_id=attendee.id, kind="updated",
                             title="A", body="x"),
            UserNotification(user_id=attendee.id, kind="updated",
                             title="B", body="y"),
        ])
        db.commit()
        resp = client.post("/api/notifications/feed/read-all",
                          cookies=auth_cookie(attendee))
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2
        feed = client.get("/api/notifications/feed",
                         cookies=auth_cookie(attendee)).json()
        assert feed["unread_count"] == 0


class TestPushSubscriptions:
    SUB = {"endpoint": "https://push.example/abc",
           "keys": {"p256dh": "key123", "auth": "auth123"}}

    def test_key_unauthenticated_blocked(self, client):
        assert client.get("/api/notifications/push/key").status_code == 401

    def test_key_disabled_by_default(self, client, attendee):
        r = client.get("/api/notifications/push/key",
                       cookies=auth_cookie(attendee))
        assert r.status_code == 200
        assert r.json()["enabled"] is False

    def test_subscribe_then_upsert(self, client, attendee, db):
        r = client.post("/api/notifications/push/subscribe", json=self.SUB,
                        cookies=auth_cookie(attendee))
        assert r.status_code == 200
        assert db.query(PushSubscription).count() == 1
        # Re-subscribing with the same endpoint upserts, no duplicate row.
        r2 = client.post("/api/notifications/push/subscribe", json=self.SUB,
                         cookies=auth_cookie(attendee))
        assert r2.status_code == 200
        assert db.query(PushSubscription).count() == 1

    def test_subscribe_requires_auth(self, client):
        assert client.post("/api/notifications/push/subscribe",
                          json=self.SUB).status_code == 401

    def test_unsubscribe(self, client, attendee, db):
        client.post("/api/notifications/push/subscribe", json=self.SUB,
                    cookies=auth_cookie(attendee))
        r = client.post("/api/notifications/push/unsubscribe",
                       json={"endpoint": self.SUB["endpoint"]},
                       cookies=auth_cookie(attendee))
        assert r.status_code == 200
        assert db.query(PushSubscription).count() == 0


class TestPushDelivery:
    def _enable(self, monkeypatch):
        from app.core import push_service
        monkeypatch.setattr(push_service.settings, "VAPID_PUBLIC_KEY", "pub")
        monkeypatch.setattr(push_service.settings, "VAPID_PRIVATE_KEY", "priv")
        return push_service

    def test_disabled_is_noop(self, monkeypatch, db, attendee):
        from app.core import push_service
        monkeypatch.setattr(push_service.settings, "VAPID_PUBLIC_KEY", "")
        monkeypatch.setattr(push_service.settings, "VAPID_PRIVATE_KEY", "")
        db.add(PushSubscription(user_id=attendee.id, endpoint="https://p/1",
                                p256dh="p", auth="a"))
        db.commit()
        assert push_service.send_push_to_users(db, [attendee.id], {"title": "T"}) == 0

    def test_sends_to_subscriptions(self, monkeypatch, db, attendee):
        push_service = self._enable(monkeypatch)
        calls = []
        monkeypatch.setattr(push_service, "_webpush", lambda **kw: calls.append(kw))
        db.add(PushSubscription(user_id=attendee.id, endpoint="https://p/2",
                                p256dh="p", auth="a"))
        db.commit()
        sent = push_service.send_push_to_users(db, [attendee.id], {"title": "T", "body": "B"})
        assert sent == 1
        assert len(calls) == 1
        assert calls[0]["subscription_info"]["endpoint"] == "https://p/2"

    def test_stale_subscription_pruned(self, monkeypatch, db, attendee):
        push_service = self._enable(monkeypatch)

        class Resp:
            status_code = 410

        def boom(**kw):
            raise push_service.WebPushException("gone", response=Resp())

        monkeypatch.setattr(push_service, "_webpush", boom)
        db.add(PushSubscription(user_id=attendee.id, endpoint="https://p/3",
                                p256dh="p", auth="a"))
        db.commit()
        sent = push_service.send_push_to_users(db, [attendee.id], {"title": "T"})
        assert sent == 0
        assert db.query(PushSubscription).filter(
            PushSubscription.endpoint == "https://p/3").count() == 0
