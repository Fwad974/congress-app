"""
Tests for the congress schedule feature.

Public viewing for any authenticated user; admin-only mutations.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.schedule import ScheduleItem, ScheduleType
from tests.conftest import auth_cookie


def _payload(**overrides):
    start = datetime.now(timezone.utc) + timedelta(days=1)
    end = start + timedelta(hours=1)
    base = {
        "title": "Opening Keynote",
        "description": "Welcome address",
        "type": "keynote",
        "speaker": "Dr. Example",
        "location": "Main Hall",
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
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
        assert data["speaker"] == "Dr. Example"

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
            json={"title": "Updated Title", "speaker": "New Speaker"},
            cookies=auth_cookie(admin_user),
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated Title"
        assert resp.json()["speaker"] == "New Speaker"

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
