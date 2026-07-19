"""
Tests for user notes + admin (super admin) moderation.
"""
from datetime import datetime, timedelta, timezone

from app.models.schedule import ScheduleItem, ScheduleType
from app.models.note import Note
from app.models.audit_log import AuditLog, AuditAction
from tests.conftest import auth_cookie, make_user
from app.models.user import UserRole


def _make_item(db, title="Session"):
    now = datetime.now(timezone.utc)
    item = ScheduleItem(
        title=title, type=ScheduleType.talk,
        start_time=now + timedelta(hours=2),
        end_time=now + timedelta(hours=3),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


class TestUserNotes:
    def test_unauthenticated_blocked(self, client):
        assert client.get("/api/notes").status_code == 401
        assert client.post("/api/notes", json={"content": "hi"}).status_code == 401

    def test_create_standalone_note(self, client, attendee):
        r = client.post("/api/notes", json={"content": "  Remember this  "},
                        cookies=auth_cookie(attendee))
        assert r.status_code == 201
        body = r.json()
        assert body["content"] == "Remember this"   # trimmed
        assert body["schedule_item_id"] is None
        assert body["session_title"] is None

    def test_create_linked_note(self, client, attendee, db):
        item = _make_item(db, "Stem Cell Keynote")
        r = client.post("/api/notes",
                        json={"content": "Great talk", "schedule_item_id": item.id},
                        cookies=auth_cookie(attendee))
        assert r.status_code == 201
        body = r.json()
        assert body["schedule_item_id"] == item.id
        assert body["session_title"] == "Stem Cell Keynote"

    def test_create_with_bad_session_rejected(self, client, attendee):
        r = client.post("/api/notes",
                        json={"content": "x", "schedule_item_id": 99999},
                        cookies=auth_cookie(attendee))
        assert r.status_code == 400

    def test_empty_content_rejected(self, client, attendee):
        r = client.post("/api/notes", json={"content": "   "},
                        cookies=auth_cookie(attendee))
        assert r.status_code == 422

    def test_list_is_per_user(self, client, attendee, db):
        other = make_user(db, email="other@test.com", role=UserRole.attendee)
        client.post("/api/notes", json={"content": "mine"},
                    cookies=auth_cookie(attendee))
        client.post("/api/notes", json={"content": "theirs"},
                    cookies=auth_cookie(other))
        body = client.get("/api/notes", cookies=auth_cookie(attendee)).json()
        assert [n["content"] for n in body["notes"]] == ["mine"]
        assert body["total"] == 1

    def test_list_filter_by_session(self, client, attendee, db):
        item = _make_item(db)
        client.post("/api/notes", json={"content": "linked", "schedule_item_id": item.id},
                    cookies=auth_cookie(attendee))
        client.post("/api/notes", json={"content": "standalone"},
                    cookies=auth_cookie(attendee))
        body = client.get(f"/api/notes?schedule_item_id={item.id}",
                          cookies=auth_cookie(attendee)).json()
        assert [n["content"] for n in body["notes"]] == ["linked"]

    def test_update_own_note(self, client, attendee, db):
        nid = client.post("/api/notes", json={"content": "draft"},
                          cookies=auth_cookie(attendee)).json()["id"]
        r = client.put(f"/api/notes/{nid}", json={"content": "final"},
                       cookies=auth_cookie(attendee))
        assert r.status_code == 200
        assert r.json()["content"] == "final"

    def test_update_can_unlink_session(self, client, attendee, db):
        item = _make_item(db)
        nid = client.post("/api/notes",
                          json={"content": "x", "schedule_item_id": item.id},
                          cookies=auth_cookie(attendee)).json()["id"]
        r = client.put(f"/api/notes/{nid}", json={"schedule_item_id": None},
                       cookies=auth_cookie(attendee))
        assert r.status_code == 200
        assert r.json()["schedule_item_id"] is None

    def test_cannot_update_others_note(self, client, attendee, db):
        other = make_user(db, email="o2@test.com", role=UserRole.attendee)
        nid = client.post("/api/notes", json={"content": "theirs"},
                          cookies=auth_cookie(other)).json()["id"]
        r = client.put(f"/api/notes/{nid}", json={"content": "hacked"},
                       cookies=auth_cookie(attendee))
        assert r.status_code == 404

    def test_delete_own_note(self, client, attendee):
        nid = client.post("/api/notes", json={"content": "bye"},
                          cookies=auth_cookie(attendee)).json()["id"]
        assert client.delete(f"/api/notes/{nid}",
                            cookies=auth_cookie(attendee)).status_code == 200
        assert client.get("/api/notes",
                         cookies=auth_cookie(attendee)).json()["total"] == 0

    def test_cannot_delete_others_note(self, client, attendee, db):
        other = make_user(db, email="o3@test.com", role=UserRole.attendee)
        nid = client.post("/api/notes", json={"content": "theirs"},
                          cookies=auth_cookie(other)).json()["id"]
        assert client.delete(f"/api/notes/{nid}",
                            cookies=auth_cookie(attendee)).status_code == 404


class TestAdminNotes:
    def test_admin_cannot_view_notes(self, client, admin_user, attendee):
        # Only super_admin may moderate notes.
        client.post("/api/notes", json={"content": "x"}, cookies=auth_cookie(attendee))
        assert client.get("/api/admin/notes",
                         cookies=auth_cookie(admin_user)).status_code == 403

    def test_attendee_cannot_view_notes(self, client, attendee):
        assert client.get("/api/notes").status_code == 401
        assert client.get("/api/admin/notes",
                         cookies=auth_cookie(attendee)).status_code == 403

    def test_super_admin_views_all_notes(self, client, super_admin, attendee, db):
        other = make_user(db, email="o4@test.com", role=UserRole.attendee)
        client.post("/api/notes", json={"content": "alpha"}, cookies=auth_cookie(attendee))
        client.post("/api/notes", json={"content": "beta"}, cookies=auth_cookie(other))
        body = client.get("/api/admin/notes", cookies=auth_cookie(super_admin)).json()
        assert body["total"] == 2
        contents = {n["content"] for n in body["notes"]}
        assert contents == {"alpha", "beta"}
        # Author info is attached.
        assert all(n["user_email"] for n in body["notes"])

    def test_super_admin_search_notes(self, client, super_admin, attendee, db):
        client.post("/api/notes", json={"content": "find-me-needle"},
                    cookies=auth_cookie(attendee))
        client.post("/api/notes", json={"content": "unrelated"},
                    cookies=auth_cookie(attendee))
        body = client.get("/api/admin/notes?search=needle",
                          cookies=auth_cookie(super_admin)).json()
        assert body["total"] == 1
        assert body["notes"][0]["content"] == "find-me-needle"

    def test_super_admin_deletes_note_and_logs(self, client, super_admin, attendee, db):
        nid = client.post("/api/notes", json={"content": "delete me"},
                          cookies=auth_cookie(attendee)).json()["id"]
        r = client.delete(f"/api/admin/notes/{nid}", cookies=auth_cookie(super_admin))
        assert r.status_code == 200
        assert db.query(Note).filter(Note.id == nid).count() == 0
        log = db.query(AuditLog).filter(
            AuditLog.action == AuditAction.note_delete).first()
        assert log is not None
        assert log.target_type == "note"

    def test_admin_cannot_delete_note(self, client, admin_user, attendee):
        nid = client.post("/api/notes", json={"content": "x"},
                          cookies=auth_cookie(attendee)).json()["id"]
        assert client.delete(f"/api/admin/notes/{nid}",
                            cookies=auth_cookie(admin_user)).status_code == 403


class TestNotesPage:
    def test_notes_page_requires_login(self, client):
        r = client.get("/notes", follow_redirects=False)
        assert r.status_code == 302

    def test_notes_page_renders(self, client, attendee):
        r = client.get("/notes", cookies=auth_cookie(attendee))
        assert r.status_code == 200
        assert "My" in r.text and "Notes" in r.text

    def test_super_admin_dashboard_has_notes_tab(self, client, super_admin):
        r = client.get("/admin", cookies=auth_cookie(super_admin))
        assert r.status_code == 200
        assert "User Notes" in r.text

    def test_admin_dashboard_hides_notes_tab(self, client, admin_user):
        r = client.get("/admin", cookies=auth_cookie(admin_user))
        assert r.status_code == 200
        assert 'data-tab="notes"' not in r.text
