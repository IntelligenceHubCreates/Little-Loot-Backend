import uuid
from typing import Optional, List

import cloudinary
import cloudinary.uploader
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.users.utils import JWTBearer
from app.feedback.models import CustomerFeedback
from app.settings import settings

cloudinary.config(
    cloud_name=settings.cloudinary_cloud_name,
    api_key=settings.cloudinary_api_key,
    api_secret=settings.cloudinary_api_secret,
)

public_feedback_router  = APIRouter(prefix="/api/feedback",       tags=["Feedback"])
admin_feedback_router   = APIRouter(prefix="/api/admin/feedback",  tags=["Admin - Feedback"])


def _admin(user):
    if not user or user.get("role") != 1:
        raise HTTPException(status_code=401, detail="Unauthorised")


def _serialize(f: CustomerFeedback) -> dict:
    return {
        "id":            str(f.id),
        "customer_name": f.customer_name,
        "image_url":     f.image_url,
        "video_url":     f.video_url,
        "thumbnail_url": f.thumbnail_url,
        "caption":       f.caption or "",
        "is_active":     f.is_active,
        "display_order": f.display_order,
        "created_at":    f.created_at.isoformat() if f.created_at else None,
    }


# ── Public endpoint ────────────────────────────────────────────────────────────

@public_feedback_router.get("")
def list_active_feedback(db: Session = Depends(get_db)):
    items = (
        db.query(CustomerFeedback)
        .filter(CustomerFeedback.is_active == True)
        .order_by(CustomerFeedback.display_order.asc(), CustomerFeedback.created_at.desc())
        .limit(30)
        .all()
    )
    return [_serialize(i) for i in items]


# ── Admin CRUD ─────────────────────────────────────────────────────────────────

class FeedbackCreate(BaseModel):
    customer_name: str
    image_url:     Optional[str] = None
    video_url:     Optional[str] = None
    thumbnail_url: Optional[str] = None
    caption:       Optional[str] = None
    display_order: int = 0


class FeedbackUpdate(BaseModel):
    customer_name: Optional[str] = None
    image_url:     Optional[str] = None
    video_url:     Optional[str] = None
    thumbnail_url: Optional[str] = None
    caption:       Optional[str] = None
    is_active:     Optional[bool] = None
    display_order: Optional[int]  = None


@admin_feedback_router.get("")
def admin_list_feedback(
    db:   Session = Depends(get_db),
    user: dict    = Depends(JWTBearer()),
):
    _admin(user)
    items = (
        db.query(CustomerFeedback)
        .order_by(CustomerFeedback.display_order.asc(), CustomerFeedback.created_at.desc())
        .all()
    )
    return [_serialize(i) for i in items]


@admin_feedback_router.post("", status_code=201)
def admin_create_feedback(
    body: FeedbackCreate,
    db:   Session = Depends(get_db),
    user: dict    = Depends(JWTBearer()),
):
    _admin(user)
    if not body.image_url and not body.video_url:
        raise HTTPException(status_code=422, detail="Provide at least an image_url or video_url")
    item = CustomerFeedback(
        customer_name = body.customer_name.strip(),
        image_url     = body.image_url,
        video_url     = body.video_url,
        thumbnail_url = body.thumbnail_url,
        caption       = body.caption,
        display_order = body.display_order,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _serialize(item)


@admin_feedback_router.put("/{item_id}")
def admin_update_feedback(
    item_id: uuid.UUID,
    body:    FeedbackUpdate,
    db:      Session = Depends(get_db),
    user:    dict    = Depends(JWTBearer()),
):
    _admin(user)
    item = db.query(CustomerFeedback).filter(CustomerFeedback.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Feedback item not found")
    for field, val in body.model_dump(exclude_unset=True).items():
        setattr(item, field, val)
    db.commit()
    db.refresh(item)
    return _serialize(item)


@admin_feedback_router.delete("/{item_id}", status_code=204)
def admin_delete_feedback(
    item_id: uuid.UUID,
    db:      Session = Depends(get_db),
    user:    dict    = Depends(JWTBearer()),
):
    _admin(user)
    item = db.query(CustomerFeedback).filter(CustomerFeedback.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Feedback item not found")
    db.delete(item)
    db.commit()


# ── Admin: Cloudinary upload endpoint ─────────────────────────────────────────

@admin_feedback_router.post("/upload-media")
async def upload_feedback_media(
    file: UploadFile = File(...),
    user: dict       = Depends(JWTBearer()),
):
    _admin(user)
    content_type = file.content_type or ""
    is_video     = content_type.startswith("video/")
    is_image     = content_type.startswith("image/")
    if not is_video and not is_image:
        raise HTTPException(status_code=422, detail="Only image or video files are accepted")

    data = await file.read()
    try:
        result = cloudinary.uploader.upload(
            data,
            folder          = "littleloot/feedback",
            resource_type   = "video" if is_video else "image",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cloudinary upload failed: {e}")

    secure_url = result.get("secure_url", "")
    thumbnail  = None
    if is_video:
        # Derive a JPEG thumbnail from the video public_id
        pub = result.get("public_id", "")
        thumbnail = cloudinary.CloudinaryVideo(pub).build_url(
            format="jpg", transformation=[{"width": 200, "crop": "fill"}]
        )

    return {
        "url":           secure_url,
        "thumbnail_url": thumbnail,
        "resource_type": "video" if is_video else "image",
    }
