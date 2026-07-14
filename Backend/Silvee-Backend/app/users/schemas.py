from pydantic import BaseModel, EmailStr, field_validator
from typing import List, Optional
from datetime import datetime


class UserBase(BaseModel):
    email: EmailStr
    name: str
    phone: str


class UserCreate(UserBase):
    password: str

    @field_validator('password')
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        return v


class UserResponse(UserBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True


# ── Google Login ────────────────────────────────────────────────────────────
# Field names must match exactly what [...nextauth].ts sends in the POST body.

class GoogleLoginRequest(BaseModel):
    email: str
    name: str
    google_id: str                        # account.providerAccountId from NextAuth
    image: Optional[str] = None
    google_id_token: Optional[str] = None
    google_access_token: Optional[str] = None


# ── Address ─────────────────────────────────────────────────────────────────
# Now includes full_name, phone and address_type so the data the frontend
# collects is actually persisted (previously these were silently dropped).

class AddressBase(BaseModel):
    full_name:     str
    phone:         str
    address_line1: str
    address_line2: Optional[str] = ""
    city:          str
    state:         str
    postal_code:   str
    country:       str = "India"
    address_type:  str = "home"   # 'home' | 'work' | 'other'
    is_default:    bool = False


class AddressCreate(AddressBase):
    pass


class AddressResponse(AddressBase):
    id: str
    user_id: str
    created_at: datetime

    class Config:
        from_attributes = True


# ── Profile ──────────────────────────────────────────────────────────────────

class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None


class ProfileResponse(UserResponse):
    addresses: List[AddressResponse] = []


# ── Auth ─────────────────────────────────────────────────────────────────────

class requestdetails(BaseModel):
    email: str
    password: str


class TokenSchema(BaseModel):
    access_token: str


class changepassword(BaseModel):
    email: str
    old_password: str
    new_password: str


class TokenCreate(BaseModel):
    user_id: str
    access_token: str
    refresh_token: str
    status: bool
    created_date: datetime


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str