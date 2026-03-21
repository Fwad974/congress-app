from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, List
from datetime import datetime


# ---------- Auth ----------
class SignUpRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    institution: Optional[str] = None
    research_interests: Optional[List[str]] = []

    @field_validator("password")
    @classmethod
    def password_strength(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain an uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain a number")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    institution: Optional[str] = None
    bio: Optional[str] = None
    research_interests: Optional[List[str]] = None
    orcid_id: Optional[str] = None


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain an uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain a number")
        return v


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str
    institution: Optional[str] = None
    role: str
    research_interests: Optional[List[str]] = []
    profile_photo_url: Optional[str] = None
    is_verified: bool
    created_at: datetime

    class Config:
        from_attributes = True
