"""
Tests for Pydantic schema validation.
"""
import pytest
from pydantic import ValidationError

from app.schemas.user import SignUpRequest, LoginRequest, PasswordChangeRequest, ProfileUpdateRequest
from app.schemas.admin import AdminUserCreate, AdminRoleChange, AdminBulkAction


class TestSignUpSchema:
    def test_valid_signup(self):
        req = SignUpRequest(email="a@b.com", password="Valid123", full_name="Test")
        assert req.email == "a@b.com"

    def test_password_too_short(self):
        with pytest.raises(ValidationError):
            SignUpRequest(email="a@b.com", password="Ab1", full_name="Test")

    def test_password_no_uppercase(self):
        with pytest.raises(ValidationError):
            SignUpRequest(email="a@b.com", password="nouppercase1", full_name="Test")

    def test_password_no_digit(self):
        with pytest.raises(ValidationError):
            SignUpRequest(email="a@b.com", password="NoDigitHere", full_name="Test")

    def test_invalid_email(self):
        with pytest.raises(ValidationError):
            SignUpRequest(email="not-email", password="Valid123", full_name="Test")

    def test_defaults(self):
        req = SignUpRequest(email="a@b.com", password="Valid123", full_name="Test")
        assert req.institution is None
        assert req.research_interests == []


class TestPasswordChangeSchema:
    def test_valid(self):
        req = PasswordChangeRequest(current_password="Old12345", new_password="New12345")
        assert req.new_password == "New12345"

    def test_weak_new_password(self):
        with pytest.raises(ValidationError):
            PasswordChangeRequest(current_password="Old12345", new_password="weak")


class TestAdminUserCreateSchema:
    def test_valid(self):
        req = AdminUserCreate(
            email="a@b.com", password="Valid123",
            full_name="Test", role="admin",
        )
        assert req.role == "admin"

    def test_invalid_role(self):
        with pytest.raises(ValidationError):
            AdminUserCreate(
                email="a@b.com", password="Valid123",
                full_name="Test", role="god",
            )

    def test_default_role_is_attendee(self):
        req = AdminUserCreate(
            email="a@b.com", password="Valid123", full_name="Test",
        )
        assert req.role == "attendee"


class TestAdminRoleChangeSchema:
    def test_valid_role(self):
        req = AdminRoleChange(role="moderator", reason="Promotion")
        assert req.role == "moderator"

    def test_invalid_role(self):
        with pytest.raises(ValidationError):
            AdminRoleChange(role="invalid_role")

    def test_reason_optional(self):
        req = AdminRoleChange(role="speaker")
        assert req.reason is None


class TestProfileUpdateSchema:
    def test_all_optional(self):
        req = ProfileUpdateRequest()
        assert req.full_name is None
        assert req.bio is None

    def test_partial_update(self):
        req = ProfileUpdateRequest(full_name="New Name")
        assert req.full_name == "New Name"
        assert req.institution is None
