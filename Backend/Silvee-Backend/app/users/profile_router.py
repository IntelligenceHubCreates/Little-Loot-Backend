# app/users/profile_router.py
# Profile read/update + avatar upload. Header-aware via the fixed get_current_user.

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel, EmailStr

from app.db import get_db
from app.users.utils import get_current_user, get_user_by_id
from app.users.models import Users

# Cloudinary — optional, only used if installed + configured
try:
    import cloudinary
    import cloudinary.uploader
    _CLOUDINARY_AVAILABLE = True
except ImportError:
    _CLOUDINARY_AVAILABLE = False

router = APIRouter(prefix="/api/user", tags=["user"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ProfileBase(BaseModel):
    """Original schema — kept for backward compat."""
    name:  str
    email: EmailStr
    phone: str


class ProfileUpdateFull(BaseModel):
    """Flexible schema — all fields optional, sent by AccountPage frontend."""
    name:            Optional[str]       = None
    first_name:      Optional[str]       = None
    last_name:       Optional[str]       = None
    email:           Optional[EmailStr]  = None
    phone:           Optional[str]       = None
    dob:             Optional[str]       = None
    gender:          Optional[str]       = None
    profile_picture: Optional[str]       = None  # Cloudinary URL only
    avatar_url:      Optional[str]       = None  # alias


def _user_dict(user: Users) -> dict:
    """Safely serialise a Users ORM object to a plain dict.

    profile_picture falls back to the Google profile_image so OAuth users who
    never uploaded a custom avatar still get a hosted photo URL from the backend.
    """
    return {
        "id":              str(getattr(user, "id", "")),
        "name":            getattr(user, "name", None),
        "email":           getattr(user, "email", None),
        "phone":           getattr(user, "phone", None),
        "dob":             getattr(user, "dob", None),
        "gender":          getattr(user, "gender", None),
        "profile_picture": getattr(user, "profile_picture", None) or getattr(user, "profile_image", None),
        "role":            getattr(user, "role", None),
    }


def _resolve_uid(current_user) -> str:
    return current_user["id"] if isinstance(current_user, dict) else current_user.id


# ── GET /api/user/profile ─────────────────────────────────────────────────────

@router.get("/profile")
async def get_profile(request: Request, db: Session = Depends(get_db)):
    current_user, error_message = await get_current_user(request, db)
    if error_message or not current_user:
        raise HTTPException(status_code=401, detail=error_message or "Not authenticated")

    user_obj = get_user_by_id(db, _resolve_uid(current_user))
    if not user_obj:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_dict(user_obj)


# ── PUT /api/user/profile ─────────────────────────────────────────────────────

@router.put("/profile")
async def update_profile(
    profile: ProfileUpdateFull,
    request: Request,
    db:      Session = Depends(get_db),
):
    current_user, error_message = await get_current_user(request, db, True)
    if error_message or not current_user:
        raise HTTPException(status_code=401, detail=error_message or "Not authenticated")

    uid  = _resolve_uid(current_user)
    user: Users = get_user_by_id(db, uid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        # Name — prefer first_name+last_name combo, fall back to full name
        if profile.first_name is not None or profile.last_name is not None:
            first = (profile.first_name or "").strip()
            last  = (profile.last_name  or "").strip()
            combined = f"{first} {last}".strip()
            if combined:
                user.name = combined
        elif profile.name is not None and profile.name.strip():
            user.name = profile.name.strip()

        if profile.email is not None and profile.email:
            user.email = str(profile.email)
        if profile.phone is not None:
            user.phone = profile.phone.strip()

        if hasattr(user, "dob") and profile.dob is not None:
            user.dob = profile.dob
        if hasattr(user, "gender") and profile.gender is not None:
            user.gender = profile.gender

        # Only store a proper https:// URL — never a raw base64 blob
        avatar_url = profile.avatar_url or profile.profile_picture
        if avatar_url and avatar_url.startswith("http") and hasattr(user, "profile_picture"):
            user.profile_picture = avatar_url

        db.commit()
        db.refresh(user)
        return {"message": "Updated Successfully", **_user_dict(user)}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


# ── PATCH /api/user/profile ───────────────────────────────────────────────────

@router.patch("/profile")
async def patch_profile(
    profile: ProfileUpdateFull,
    request: Request,
    db:      Session = Depends(get_db),
):
    """Identical to PUT — frontend falls back to PATCH if PUT returns non-2xx."""
    return await update_profile(profile, request, db)


# ── POST /api/user/avatar ─────────────────────────────────────────────────────

@router.post("/avatar")
async def upload_avatar(
    request: Request,
    db:      Session    = Depends(get_db),
    avatar:  UploadFile = File(None),
    file:    UploadFile = File(None),
):
    current_user, error_message = await get_current_user(request, db, True)
    if error_message or not current_user:
        raise HTTPException(status_code=401, detail=error_message or "Not authenticated")

    upload_file = avatar or file
    if not upload_file:
        raise HTTPException(status_code=400, detail="No file provided")
    if not upload_file.content_type or not upload_file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    contents = await upload_file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 5 MB)")

    uid  = _resolve_uid(current_user)
    user: Users = get_user_by_id(db, uid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not _CLOUDINARY_AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail="Cloudinary not installed. Run: pip install cloudinary"
        )

    try:
        result = cloudinary.uploader.upload(
            contents,
            folder="littleloot/avatars",
            public_id=f"user_{user.id}_avatar",
            overwrite=True,
            transformation=[
                {"width": 400, "height": 400, "crop": "fill", "gravity": "face"},
                {"quality": "auto", "fetch_format": "auto"},
            ],
        )
        url: str = result.get("secure_url", "")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Cloudinary upload failed: {str(e)}")

    try:
        if hasattr(user, "profile_picture"):
            user.profile_picture = url
            db.commit()
            db.refresh(user)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB save failed: {str(e)}")

    return {"message": "Avatar updated", "url": url, "profile_picture": url, **_user_dict(user)}

# ── POST /api/user/change-password ─────────────────────────────────────────────

from app.users.services import verify_password, get_hashed_password


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user, error_message = await get_current_user(request, db, True)  # ORM object
    if error_message or not current_user:
        raise HTTPException(status_code=401, detail=error_message or "Not authenticated")

    user: Users = current_user

    if not getattr(user, "hashed_password", None):
        raise HTTPException(
            status_code=400,
            detail="This account uses Google sign-in. Password change is not available here.",
        )
    if not verify_password(body.old_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    if body.old_password == body.new_password:
        raise HTTPException(status_code=400, detail="New password must differ from the current one")

    user.hashed_password = get_hashed_password(body.new_password)
    db.commit()
    return {"message": "Password changed successfully"}