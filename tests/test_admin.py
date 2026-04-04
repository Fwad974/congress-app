"""
Tests for admin API endpoints: user CRUD, RBAC, bulk actions, CSV export, audit logs.
"""
import pytest
from app.models.user import User, UserRole
from tests.conftest import make_user, auth_cookie


class TestAdminStats:
    def test_stats_as_admin(self, client, admin_user):
        resp = client.get("/api/admin/stats", cookies=auth_cookie(admin_user))
        assert resp.status_code == 200
        data = resp.json()
        assert "total_users" in data
        assert "active_users" in data
        assert "roles_breakdown" in data

    def test_stats_as_attendee_forbidden(self, client, attendee):
        resp = client.get("/api/admin/stats", cookies=auth_cookie(attendee))
        assert resp.status_code == 403

    def test_stats_unauthenticated(self, client):
        resp = client.get("/api/admin/stats")
        assert resp.status_code == 401


class TestListUsers:
    def test_list_users(self, client, admin_user, db):
        make_user(db, email="u1@test.com", full_name="User One")
        make_user(db, email="u2@test.com", full_name="User Two")
        resp = client.get("/api/admin/users", cookies=auth_cookie(admin_user))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 3  # admin + 2 users
        assert len(data["users"]) >= 3

    def test_list_users_search(self, client, admin_user, db):
        make_user(db, email="searchme@test.com", full_name="Findable User")
        resp = client.get("/api/admin/users?search=Findable", cookies=auth_cookie(admin_user))
        assert resp.status_code == 200
        assert any(u["full_name"] == "Findable User" for u in resp.json()["users"])

    def test_list_users_filter_role(self, client, admin_user, db):
        make_user(db, email="speaker1@test.com", role=UserRole.speaker, full_name="Speaker")
        resp = client.get("/api/admin/users?role=speaker", cookies=auth_cookie(admin_user))
        assert resp.status_code == 200
        for u in resp.json()["users"]:
            assert u["role"] == "speaker"

    def test_list_users_pagination(self, client, admin_user, db):
        for i in range(5):
            make_user(db, email=f"page{i}@test.com", full_name=f"Page User {i}")
        resp = client.get("/api/admin/users?per_page=2&page=1", cookies=auth_cookie(admin_user))
        data = resp.json()
        assert len(data["users"]) == 2
        assert data["pages"] >= 3


class TestGetUser:
    def test_get_user_found(self, client, admin_user, db):
        user = make_user(db, email="target@test.com", full_name="Target")
        resp = client.get(f"/api/admin/users/{user.id}", cookies=auth_cookie(admin_user))
        assert resp.status_code == 200
        assert resp.json()["email"] == "target@test.com"

    def test_get_user_not_found(self, client, admin_user):
        resp = client.get("/api/admin/users/99999", cookies=auth_cookie(admin_user))
        assert resp.status_code == 404


class TestCreateUser:
    def test_create_user_as_admin(self, client, admin_user):
        resp = client.post("/api/admin/users", json={
            "email": "created@test.com",
            "password": "NewPass123",
            "full_name": "Created User",
            "role": "attendee",
        }, cookies=auth_cookie(admin_user))
        assert resp.status_code == 201
        assert resp.json()["email"] == "created@test.com"

    def test_create_user_duplicate_email(self, client, admin_user, db):
        make_user(db, email="exists@test.com")
        resp = client.post("/api/admin/users", json={
            "email": "exists@test.com",
            "password": "NewPass123",
            "full_name": "Dup",
            "role": "attendee",
        }, cookies=auth_cookie(admin_user))
        assert resp.status_code == 400

    def test_admin_cannot_create_super_admin(self, client, admin_user):
        """ROLE-01: Only super_admin can create super_admin."""
        resp = client.post("/api/admin/users", json={
            "email": "newsa@test.com",
            "password": "NewPass123",
            "full_name": "New SA",
            "role": "super_admin",
        }, cookies=auth_cookie(admin_user))
        assert resp.status_code == 403

    def test_admin_cannot_create_admin(self, client, admin_user):
        """ROLE-02: Admin can only assign up to moderator."""
        resp = client.post("/api/admin/users", json={
            "email": "newadmin@test.com",
            "password": "NewPass123",
            "full_name": "New Admin",
            "role": "admin",
        }, cookies=auth_cookie(admin_user))
        assert resp.status_code == 403

    def test_super_admin_can_create_super_admin(self, client, super_admin):
        resp = client.post("/api/admin/users", json={
            "email": "newsa@test.com",
            "password": "NewPass123",
            "full_name": "New SA",
            "role": "super_admin",
        }, cookies=auth_cookie(super_admin))
        assert resp.status_code == 201
        assert resp.json()["role"] == "super_admin"

    def test_super_admin_can_create_admin(self, client, super_admin):
        resp = client.post("/api/admin/users", json={
            "email": "newadmin@test.com",
            "password": "NewPass123",
            "full_name": "New Admin",
            "role": "admin",
        }, cookies=auth_cookie(super_admin))
        assert resp.status_code == 201

    def test_attendee_cannot_create_user(self, client, attendee):
        resp = client.post("/api/admin/users", json={
            "email": "nope@test.com",
            "password": "NewPass123",
            "full_name": "Nope",
            "role": "attendee",
        }, cookies=auth_cookie(attendee))
        assert resp.status_code == 403


class TestUpdateUser:
    def test_update_user_profile(self, client, admin_user, db):
        user = make_user(db, email="edit@test.com", full_name="Original")
        resp = client.put(f"/api/admin/users/{user.id}", json={
            "full_name": "Edited Name",
        }, cookies=auth_cookie(admin_user))
        assert resp.status_code == 200
        assert resp.json()["full_name"] == "Edited Name"

    def test_update_nonexistent_user(self, client, admin_user):
        resp = client.put("/api/admin/users/99999", json={
            "full_name": "Ghost",
        }, cookies=auth_cookie(admin_user))
        assert resp.status_code == 404


class TestChangeRole:
    def test_change_role_success(self, client, admin_user, db):
        user = make_user(db, email="promote@test.com", role=UserRole.attendee)
        resp = client.put(f"/api/admin/users/{user.id}/role", json={
            "role": "speaker",
            "reason": "Accepted as speaker",
        }, cookies=auth_cookie(admin_user))
        assert resp.status_code == 200
        assert resp.json()["role"] == "speaker"

    def test_self_role_change_prohibited(self, client, admin_user):
        """ROLE-04: Cannot change own role."""
        resp = client.put(f"/api/admin/users/{admin_user.id}/role", json={
            "role": "super_admin",
            "reason": "Self promotion",
        }, cookies=auth_cookie(admin_user))
        assert resp.status_code == 403
        assert "ROLE-04" in resp.json()["detail"]

    def test_admin_cannot_assign_super_admin_role(self, client, admin_user, db):
        user = make_user(db, email="target@test.com")
        resp = client.put(f"/api/admin/users/{user.id}/role", json={
            "role": "super_admin",
        }, cookies=auth_cookie(admin_user))
        assert resp.status_code == 403

    def test_admin_cannot_manage_same_level(self, client, admin_user, db):
        other_admin = make_user(db, email="otheradmin@test.com", role=UserRole.admin)
        resp = client.put(f"/api/admin/users/{other_admin.id}/role", json={
            "role": "moderator",
        }, cookies=auth_cookie(admin_user))
        assert resp.status_code == 403

    def test_super_admin_can_manage_admin(self, client, super_admin, db):
        admin = make_user(db, email="demote@test.com", role=UserRole.admin)
        resp = client.put(f"/api/admin/users/{admin.id}/role", json={
            "role": "moderator",
            "reason": "Demotion",
        }, cookies=auth_cookie(super_admin))
        assert resp.status_code == 200
        assert resp.json()["role"] == "moderator"


class TestSuspendUser:
    def test_suspend_user(self, client, admin_user, db):
        user = make_user(db, email="suspend@test.com")
        resp = client.post(f"/api/admin/users/{user.id}/suspend", json={
            "reason": "Violation",
        }, cookies=auth_cookie(admin_user))
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_cannot_suspend_self(self, client, admin_user):
        resp = client.post(f"/api/admin/users/{admin_user.id}/suspend", json={
            "reason": "Self suspend",
        }, cookies=auth_cookie(admin_user))
        assert resp.status_code == 403

    def test_cannot_suspend_higher_role(self, client, admin_user, db):
        sa = make_user(db, email="sa@test.com", role=UserRole.super_admin)
        resp = client.post(f"/api/admin/users/{sa.id}/suspend", json={
            "reason": "No way",
        }, cookies=auth_cookie(admin_user))
        assert resp.status_code == 403


class TestActivateUser:
    def test_activate_user(self, client, admin_user, db):
        user = make_user(db, email="inactive@test.com", is_active=False)
        resp = client.post(f"/api/admin/users/{user.id}/activate",
                           cookies=auth_cookie(admin_user))
        assert resp.status_code == 200
        assert resp.json()["is_active"] is True


class TestDeleteUser:
    def test_only_super_admin_can_delete(self, client, admin_user, db):
        user = make_user(db, email="del@test.com")
        resp = client.delete(f"/api/admin/users/{user.id}",
                             cookies=auth_cookie(admin_user))
        assert resp.status_code == 403

    def test_super_admin_can_delete(self, client, super_admin, db):
        user = make_user(db, email="del@test.com")
        resp = client.delete(f"/api/admin/users/{user.id}",
                             cookies=auth_cookie(super_admin))
        assert resp.status_code == 200
        assert "deleted" in resp.json()["message"].lower()

    def test_cannot_delete_self(self, client, super_admin):
        resp = client.delete(f"/api/admin/users/{super_admin.id}",
                             cookies=auth_cookie(super_admin))
        assert resp.status_code == 403

    def test_cannot_delete_last_super_admin(self, client, super_admin):
        """ROLE-01: Cannot delete the last super admin."""
        resp = client.delete(f"/api/admin/users/{super_admin.id}",
                             cookies=auth_cookie(super_admin))
        assert resp.status_code == 403


class TestBulkActions:
    def test_bulk_suspend(self, client, admin_user, db):
        u1 = make_user(db, email="b1@test.com", full_name="Bulk1")
        u2 = make_user(db, email="b2@test.com", full_name="Bulk2")
        resp = client.post("/api/admin/users/bulk", json={
            "user_ids": [u1.id, u2.id],
            "action": "suspend",
            "reason": "Bulk suspend",
        }, cookies=auth_cookie(admin_user))
        assert resp.status_code == 200
        assert resp.json()["success"] == 2

    def test_bulk_activate(self, client, admin_user, db):
        u1 = make_user(db, email="b1@test.com", is_active=False)
        resp = client.post("/api/admin/users/bulk", json={
            "user_ids": [u1.id],
            "action": "activate",
        }, cookies=auth_cookie(admin_user))
        assert resp.status_code == 200
        assert resp.json()["success"] == 1

    def test_bulk_role_change(self, client, admin_user, db):
        u1 = make_user(db, email="b1@test.com")
        resp = client.post("/api/admin/users/bulk", json={
            "user_ids": [u1.id],
            "action": "role_change",
            "role": "speaker",
        }, cookies=auth_cookie(admin_user))
        assert resp.status_code == 200
        assert resp.json()["success"] == 1

    def test_bulk_cannot_manage_higher_role(self, client, admin_user, db):
        sa = make_user(db, email="sa@test.com", role=UserRole.super_admin)
        resp = client.post("/api/admin/users/bulk", json={
            "user_ids": [sa.id],
            "action": "suspend",
        }, cookies=auth_cookie(admin_user))
        data = resp.json()
        assert data["failed"] == 1
        assert data["success"] == 0

    def test_bulk_nonexistent_user(self, client, admin_user):
        resp = client.post("/api/admin/users/bulk", json={
            "user_ids": [99999],
            "action": "suspend",
        }, cookies=auth_cookie(admin_user))
        data = resp.json()
        assert data["failed"] == 1


class TestExportCSV:
    def test_export_csv(self, client, admin_user, db):
        make_user(db, email="exp@test.com", full_name="Export User")
        resp = client.get("/api/admin/users/export/csv",
                          cookies=auth_cookie(admin_user))
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        content = resp.text
        assert "Email" in content
        assert "exp@test.com" in content

    def test_export_csv_forbidden_for_attendee(self, client, attendee):
        resp = client.get("/api/admin/users/export/csv",
                          cookies=auth_cookie(attendee))
        assert resp.status_code == 403


class TestAuditLogs:
    def test_get_audit_logs(self, client, admin_user, db):
        # Trigger an auditable action first
        user = make_user(db, email="audittarget@test.com")
        client.post(f"/api/admin/users/{user.id}/suspend", json={
            "reason": "Test audit",
        }, cookies=auth_cookie(admin_user))

        resp = client.get("/api/admin/audit", cookies=auth_cookie(admin_user))
        assert resp.status_code == 200
        data = resp.json()
        assert "logs" in data
        assert data["total"] >= 1

    def test_audit_logs_forbidden_for_attendee(self, client, attendee):
        resp = client.get("/api/admin/audit", cookies=auth_cookie(attendee))
        assert resp.status_code == 403


class TestUnlockUser:
    def test_unlock_user(self, client, admin_user, db):
        user = make_user(db, email="locked@test.com")
        resp = client.post(f"/api/admin/users/{user.id}/unlock",
                           cookies=auth_cookie(admin_user))
        assert resp.status_code == 200
        assert "unlocked" in resp.json()["message"].lower()

    def test_unlock_nonexistent_user(self, client, admin_user):
        resp = client.post("/api/admin/users/99999/unlock",
                           cookies=auth_cookie(admin_user))
        assert resp.status_code == 404
