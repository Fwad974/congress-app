"""
Tests for the congress schedule feature.

Public viewing for any authenticated user; admin-only mutations.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.schedule import ScheduleItem, ScheduleType, ScheduleBookmark
from tests.conftest import auth_cookie, make_user
from app.models.user import UserRole


def _payload(**overrides):
    start = datetime.now(timezone.utc) + timedelta(days=1)
    end = start + timedelta(hours=1)
    base = {
        "title": "Opening Keynote",
        "description": "Welcome address",
        "type": "keynote",
        "location": "Main Hall",
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "extra": {"speaker": "Dr. Example", "affiliation": "ACME Lab"},
    }
    base.update(overrides)
    return base


class TestSchedulePublicListing:
    def test_unauthenticated_cannot_list(self, client):
        resp = client.get("/api/schedule")
        assert resp.status_code == 401

    def test_attendee_can_list(self, client, attendee, db):
        start = datetime.now(timezone.utc) + timedelta(days=1)
        db.add(ScheduleItem(
            title="Plenary", type=ScheduleType.talk,
            start_time=start, end_time=start + timedelta(hours=1),
        ))
        db.commit()

        resp = client.get("/api/schedule", cookies=auth_cookie(attendee))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "Plenary"

    def test_listing_sorted_by_start_time(self, client, attendee, db):
        base = datetime.now(timezone.utc) + timedelta(days=1)
        db.add(ScheduleItem(
            title="Later", type=ScheduleType.talk,
            start_time=base + timedelta(hours=2), end_time=base + timedelta(hours=3),
        ))
        db.add(ScheduleItem(
            title="Earlier", type=ScheduleType.talk,
            start_time=base, end_time=base + timedelta(hours=1),
        ))
        db.commit()

        resp = client.get("/api/schedule", cookies=auth_cookie(attendee))
        titles = [i["title"] for i in resp.json()["items"]]
        assert titles == ["Earlier", "Later"]

    def test_filter_by_type(self, client, attendee, db):
        base = datetime.now(timezone.utc) + timedelta(days=1)
        db.add(ScheduleItem(
            title="Keynote One", type=ScheduleType.keynote,
            start_time=base, end_time=base + timedelta(hours=1),
        ))
        db.add(ScheduleItem(
            title="Talk One", type=ScheduleType.talk,
            start_time=base + timedelta(hours=2), end_time=base + timedelta(hours=3),
        ))
        db.commit()

        resp = client.get("/api/schedule?type=keynote", cookies=auth_cookie(attendee))
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["type"] == "keynote"


class TestScheduleAdminCRUD:
    def test_attendee_cannot_create(self, client, attendee):
        resp = client.post("/api/schedule", json=_payload(),
                           cookies=auth_cookie(attendee))
        assert resp.status_code == 403

    def test_admin_can_create(self, client, admin_user):
        resp = client.post("/api/schedule", json=_payload(),
                           cookies=auth_cookie(admin_user))
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Opening Keynote"
        assert data["type"] == "keynote"
        assert data["extra"]["speaker"] == "Dr. Example"
        assert data["extra"]["affiliation"] == "ACME Lab"

    def test_create_rejects_end_before_start(self, client, admin_user):
        start = datetime.now(timezone.utc) + timedelta(days=1)
        resp = client.post(
            "/api/schedule",
            json=_payload(
                start_time=(start + timedelta(hours=1)).isoformat(),
                end_time=start.isoformat(),
            ),
            cookies=auth_cookie(admin_user),
        )
        assert resp.status_code == 422

    def test_create_rejects_invalid_type(self, client, admin_user):
        resp = client.post(
            "/api/schedule",
            json=_payload(type="not-a-type"),
            cookies=auth_cookie(admin_user),
        )
        assert resp.status_code == 422

    def test_create_rejects_blank_title(self, client, admin_user):
        resp = client.post(
            "/api/schedule",
            json=_payload(title="   "),
            cookies=auth_cookie(admin_user),
        )
        assert resp.status_code == 422

    def test_admin_can_update(self, client, admin_user):
        created = client.post("/api/schedule", json=_payload(),
                              cookies=auth_cookie(admin_user)).json()
        resp = client.put(
            f"/api/schedule/{created['id']}",
            json={
                "title": "Updated Title",
                "extra": {"speaker": "New Speaker", "affiliation": "Other Lab"},
            },
            cookies=auth_cookie(admin_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Updated Title"
        assert body["extra"]["speaker"] == "New Speaker"
        assert body["extra"]["affiliation"] == "Other Lab"

    def test_attendee_cannot_update(self, client, admin_user, attendee):
        created = client.post("/api/schedule", json=_payload(),
                              cookies=auth_cookie(admin_user)).json()
        resp = client.put(
            f"/api/schedule/{created['id']}",
            json={"title": "Hacked"},
            cookies=auth_cookie(attendee),
        )
        assert resp.status_code == 403

    def test_admin_can_delete(self, client, admin_user):
        created = client.post("/api/schedule", json=_payload(),
                              cookies=auth_cookie(admin_user)).json()
        resp = client.delete(f"/api/schedule/{created['id']}",
                             cookies=auth_cookie(admin_user))
        assert resp.status_code == 200

        # Confirm gone
        resp = client.get(f"/api/schedule/{created['id']}",
                          cookies=auth_cookie(admin_user))
        assert resp.status_code == 404

    def test_attendee_cannot_delete(self, client, admin_user, attendee):
        created = client.post("/api/schedule", json=_payload(),
                              cookies=auth_cookie(admin_user)).json()
        resp = client.delete(f"/api/schedule/{created['id']}",
                             cookies=auth_cookie(attendee))
        assert resp.status_code == 403

    def test_super_admin_can_create(self, client, super_admin):
        resp = client.post("/api/schedule", json=_payload(),
                           cookies=auth_cookie(super_admin))
        assert resp.status_code == 201

    def test_update_rejects_end_before_start(self, client, admin_user):
        created = client.post("/api/schedule", json=_payload(),
                              cookies=auth_cookie(admin_user)).json()
        start = datetime.now(timezone.utc) + timedelta(days=2)
        resp = client.put(
            f"/api/schedule/{created['id']}",
            json={
                "start_time": (start + timedelta(hours=1)).isoformat(),
                "end_time": start.isoformat(),
            },
            cookies=auth_cookie(admin_user),
        )
        assert resp.status_code == 400


class TestScheduleExtraFields:
    def test_unknown_extra_keys_are_dropped(self, client, admin_user):
        resp = client.post(
            "/api/schedule",
            json=_payload(extra={"speaker": "Dr. X", "evil_field": "bad", "moderator": "Y"}),
            cookies=auth_cookie(admin_user),
        )
        assert resp.status_code == 201
        extra = resp.json()["extra"]
        assert extra == {"speaker": "Dr. X"}  # type=keynote: only speaker survives

    def test_panel_accepts_panelists_as_list(self, client, admin_user):
        resp = client.post(
            "/api/schedule",
            json=_payload(type="panel", extra={
                "moderator": "Mod A",
                "panelists": ["A", "B", "C"],
            }),
            cookies=auth_cookie(admin_user),
        )
        assert resp.status_code == 201
        extra = resp.json()["extra"]
        assert extra["moderator"] == "Mod A"
        assert extra["panelists"] == ["A", "B", "C"]

    def test_panel_accepts_panelists_as_newline_string(self, client, admin_user):
        resp = client.post(
            "/api/schedule",
            json=_payload(type="panel", extra={"panelists": "Alice\nBob\n\nCarol"}),
            cookies=auth_cookie(admin_user),
        )
        assert resp.status_code == 201
        assert resp.json()["extra"]["panelists"] == ["Alice", "Bob", "Carol"]

    def test_workshop_capacity_coerces_to_int(self, client, admin_user):
        resp = client.post(
            "/api/schedule",
            json=_payload(type="workshop", extra={
                "instructor": "Prof Y",
                "capacity": "30",
                "prerequisites": "Bring a laptop",
            }),
            cookies=auth_cookie(admin_user),
        )
        assert resp.status_code == 201
        extra = resp.json()["extra"]
        assert extra["capacity"] == 30
        assert extra["instructor"] == "Prof Y"

    def test_workshop_invalid_capacity_dropped(self, client, admin_user):
        resp = client.post(
            "/api/schedule",
            json=_payload(type="workshop", extra={"capacity": "not-a-number"}),
            cookies=auth_cookie(admin_user),
        )
        assert resp.status_code == 201
        assert "capacity" not in resp.json()["extra"]

    def test_break_drops_all_extras(self, client, admin_user):
        resp = client.post(
            "/api/schedule",
            json=_payload(type="break", extra={"speaker": "anyone", "anything": "x"}),
            cookies=auth_cookie(admin_user),
        )
        assert resp.status_code == 201
        assert resp.json()["extra"] == {}

    def test_changing_type_re_filters_extras(self, client, admin_user):
        # Start as a talk with speaker
        created = client.post(
            "/api/schedule",
            json=_payload(type="talk", extra={"speaker": "Dr. T"}),
            cookies=auth_cookie(admin_user),
        ).json()
        assert created["extra"]["speaker"] == "Dr. T"

        # Change to panel without specifying extra; speaker is no longer valid
        resp = client.put(
            f"/api/schedule/{created['id']}",
            json={"type": "panel"},
            cookies=auth_cookie(admin_user),
        )
        assert resp.status_code == 200
        assert "speaker" not in resp.json()["extra"]

    def test_poster_fields(self, client, admin_user):
        resp = client.post(
            "/api/schedule",
            json=_payload(type="poster", extra={
                "presenter": "Sara",
                "poster_number": "P-12",
                "abstract": "Stem cells in regenerative medicine.",
            }),
            cookies=auth_cookie(admin_user),
        )
        assert resp.status_code == 201
        extra = resp.json()["extra"]
        assert extra["presenter"] == "Sara"
        assert extra["poster_number"] == "P-12"
        assert "abstract" in extra


class TestScheduleBookmarks:
    def _create_item(self, client, admin):
        return client.post(
            "/api/schedule", json=_payload(),
            cookies=auth_cookie(admin),
        ).json()

    def test_unauthenticated_cannot_bookmark(self, client, admin_user):
        item = self._create_item(client, admin_user)
        resp = client.post(f"/api/schedule/{item['id']}/bookmark")
        assert resp.status_code == 401

    def test_attendee_can_bookmark(self, client, admin_user, attendee):
        item = self._create_item(client, admin_user)
        resp = client.post(f"/api/schedule/{item['id']}/bookmark",
                           cookies=auth_cookie(attendee))
        assert resp.status_code == 200
        assert resp.json()["is_bookmarked"] is True

    def test_bookmark_is_idempotent(self, client, admin_user, attendee):
        item = self._create_item(client, admin_user)
        client.post(f"/api/schedule/{item['id']}/bookmark",
                    cookies=auth_cookie(attendee))
        resp = client.post(f"/api/schedule/{item['id']}/bookmark",
                           cookies=auth_cookie(attendee))
        assert resp.status_code == 200
        assert resp.json()["is_bookmarked"] is True

    def test_remove_bookmark(self, client, admin_user, attendee):
        item = self._create_item(client, admin_user)
        client.post(f"/api/schedule/{item['id']}/bookmark",
                    cookies=auth_cookie(attendee))
        resp = client.delete(f"/api/schedule/{item['id']}/bookmark",
                             cookies=auth_cookie(attendee))
        assert resp.status_code == 200
        assert resp.json()["is_bookmarked"] is False

    def test_bookmark_404_on_missing_item(self, client, attendee):
        resp = client.post("/api/schedule/9999/bookmark",
                           cookies=auth_cookie(attendee))
        assert resp.status_code == 404

    def test_listing_includes_is_bookmarked(self, client, admin_user, attendee, db):
        item_a = self._create_item(client, admin_user)
        # Create a second one so we cover both true and false
        client.post(
            "/api/schedule",
            json=_payload(title="Second"),
            cookies=auth_cookie(admin_user),
        )

        client.post(f"/api/schedule/{item_a['id']}/bookmark",
                    cookies=auth_cookie(attendee))

        resp = client.get("/api/schedule", cookies=auth_cookie(attendee))
        assert resp.status_code == 200
        items = {i["title"]: i for i in resp.json()["items"]}
        assert items["Opening Keynote"]["is_bookmarked"] is True
        assert items["Second"]["is_bookmarked"] is False

    def test_bookmark_is_per_user(self, client, admin_user, attendee, db):
        item = self._create_item(client, admin_user)
        client.post(f"/api/schedule/{item['id']}/bookmark",
                    cookies=auth_cookie(attendee))

        other = make_user(db, email="other@test.com", role=UserRole.attendee,
                          full_name="Other Attendee")
        resp = client.get(f"/api/schedule/{item['id']}",
                          cookies=auth_cookie(other))
        assert resp.json()["is_bookmarked"] is False

    def test_bookmark_filter(self, client, admin_user, attendee):
        a = self._create_item(client, admin_user)
        client.post(
            "/api/schedule",
            json=_payload(title="Not bookmarked"),
            cookies=auth_cookie(admin_user),
        )
        client.post(f"/api/schedule/{a['id']}/bookmark",
                    cookies=auth_cookie(attendee))

        resp = client.get("/api/schedule?bookmarked=true",
                          cookies=auth_cookie(attendee))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "Opening Keynote"
        assert body["items"][0]["is_bookmarked"] is True

    def test_deleting_item_cleans_up_bookmarks(self, client, admin_user, attendee, db):
        item = self._create_item(client, admin_user)
        client.post(f"/api/schedule/{item['id']}/bookmark",
                    cookies=auth_cookie(attendee))
        assert db.query(ScheduleBookmark).count() == 1

        resp = client.delete(f"/api/schedule/{item['id']}",
                             cookies=auth_cookie(admin_user))
        assert resp.status_code == 200
        assert db.query(ScheduleBookmark).count() == 0


class TestSchedulePage:
    def test_schedule_page_redirects_when_unauth(self, client):
        resp = client.get("/schedule", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    def test_schedule_page_renders_for_attendee(self, client, attendee):
        resp = client.get("/schedule", cookies=auth_cookie(attendee))
        assert resp.status_code == 200
        assert b"Congress" in resp.content
        # Attendees should not see the "Add Item" button
        assert b"+ Add Item" not in resp.content

    def test_schedule_page_admin_sees_add_button(self, client, admin_user):
        resp = client.get("/schedule", cookies=auth_cookie(admin_user))
        assert resp.status_code == 200
        assert b"+ Add Item" in resp.content
