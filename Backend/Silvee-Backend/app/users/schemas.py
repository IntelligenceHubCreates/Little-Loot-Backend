from pydantic import BaseModel, EmailStr, field_validator
from typing import List, Optional
from datetime import datetime


class UserBase(BaseModel):
    email: EmailStr
    name: str
    phone: Optional[str] = None   # signup form marks it optional; column is nullable


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
    landmark:      Optional[str] = ""
    city:          str
    state:         str
    postal_code:   str
    country:       str = "India"
    address_type:  str = "home"   # 'home' | 'work' | 'other'
    is_default:    bool = False


class AddressCreate(AddressBase):
    """Incoming address: cleaned and validated (responses use AddressBase as-is,
    so older saved rows never fail to load)."""

    @field_validator("full_name", "address_line1", "city", "state", mode="before")
    @classmethod
    def _required_text(cls, v):
        v = " ".join(str(v or "").split())
        if not v:
            raise ValueError("This field is required")
        return v[:255]

    @field_validator("address_line2", "landmark", mode="before")
    @classmethod
    def _optional_text(cls, v):
        return " ".join(str(v or "").split())[:255]

    @field_validator("phone", mode="before")
    @classmethod
    def _phone(cls, v):
        digits = "".join(ch for ch in str(v or "") if ch.isdigit())
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        if len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) != 10 or digits[0] not in "6789":
            raise ValueError("Enter a valid 10-digit mobile number")
        return digits

    @field_validator("postal_code", mode="before")
    @classmethod
    def _pincode(cls, v):
        digits = "".join(ch for ch in str(v or "") if ch.isdigit())
        if len(digits) != 6 or digits[0] == "0":
            raise ValueError("Enter a valid 6-digit pincode")
        return digits

    @field_validator("address_type", mode="before")
    @classmethod
    def _type(cls, v):
        t = str(v or "home").strip().lower()
        t = {"office": "work"}.get(t, t)
        return t if t in ("home", "work", "other") else "home"


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