"""
Tests for the notification preferences and upcoming-bookmarks API.
"""
from datetime import datetime, timedelta, timezone

from app.models.schedule import ScheduleItem, ScheduleType, ScheduleBookmark
from app.models.notification import NotificationSettings
from app.schemas.notification import DEFAULT_PREFS
from tests.conftest import auth_cookie


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
