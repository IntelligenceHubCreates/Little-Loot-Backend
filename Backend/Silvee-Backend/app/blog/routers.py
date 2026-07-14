# app/blog/routers.py
import uuid
from datetime import datetime
from typing import Optional, List

import cloudinary
import cloudinary.uploader
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.users.utils import JWTBearer
from app.blog.models import BlogPost
from app.settings import settings

cloudinary.config(
    cloud_name=settings.cloudinary_cloud_name,
    api_key=settings.cloudinary_api_key,
    api_secret=settings.cloudinary_api_secret,
)

blog_router = APIRouter(prefix="/api/admin/blog", tags=["Blog"])


def _admin(user):
    if not user or user.get("role") != 1:
        raise HTTPException(status_code=401, detail="Unauthorised")


def _slugify(s: str) -> str:
    import re
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"^-+|-+$", "", s)


def _serialize(p: BlogPost) -> dict:
    return {
        "id":         str(p.id),
        "title":      p.title,
        "slug":       p.slug,
        "excerpt":    p.excerpt or "",
        "content":    p.content or "",
        "tag":        p.tag or "",
        "image_url":  p.image_url,
        "status":     p.status,
        "views":      int(p.views or 0),
        "comments":   int(p.comments or 0),
        "likes":      int(p.likes or 0),
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


class BlogCreate(BaseModel):
    title:     str
    slug:      Optional[str] = None
    excerpt:   Optional[str] = ""
    content:   Optional[str] = ""
    tag:       Optional[str] = ""
    image_url: Optional[str] = None
    status:    str = "draft"   # 'draft' | 'published'


class BlogUpdate(BaseModel):
    title:     Optional[str]  = None
    slug:      Optional[str]  = None
    excerpt:   Optional[str]  = None
    content:   Optional[str]  = None
    tag:       Optional[str]  = None
    image_url: Optional[str]  = None
    status:    Optional[str]  = None


@blog_router.get("")
async def list_posts(
    skip:   int           = Query(0,  ge=0),
    limit:  int           = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="Filter: draft | published"),
    search: Optional[str] = Query(None),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _admin(user)
    q = session.query(BlogPost)
    if status in ("draft", "published"):
        q = q.filter(BlogPost.status == status)
    if search and search.strip():
        term = f"%{search.strip()}%"
        q = q.filter(or_(BlogPost.title.ilike(term), BlogPost.tag.ilike(term)))

    total = q.count()
    posts = q.order_by(BlogPost.created_at.desc()).offset(skip).limit(limit).all()
    return {"data": [_serialize(p) for p in posts], "totalCount": total}


@blog_router.post("", status_code=201)
async def create_post(
    body: BlogCreate,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _admin(user)
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "Title is required")

    slug = _slugify(body.slug) if body.slug else _slugify(title)
    if not slug:
        raise HTTPException(400, "Could not derive a valid slug")
    if session.query(BlogPost).filter(BlogPost.slug == slug).first():
        raise HTTPException(409, f"Slug '{slug}' already exists")
    if body.status not in ("draft", "published"):
        raise HTTPException(400, "status must be 'draft' or 'published'")

    post = BlogPost(
        title=title,
        slug=slug,
        excerpt=(body.excerpt or ""),
        content=(body.content or ""),
        tag=(body.tag or ""),
        image_url=(body.image_url or None),
        status=body.status,
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    return _serialize(post)


@blog_router.put("/{post_id}")
async def update_post(
    post_id: str,
    body: BlogUpdate,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _admin(user)
    try:
        pid = uuid.UUID(post_id)
    except ValueError:
        raise HTTPException(400, "Invalid post ID")

    post = session.query(BlogPost).filter(BlogPost.id == pid).first()
    if not post:
        raise HTTPException(404, "Post not found")

    if body.slug is not None:
        new_slug = _slugify(body.slug)
        if new_slug and new_slug != post.slug:
            if session.query(BlogPost).filter(BlogPost.slug == new_slug, BlogPost.id != pid).first():
                raise HTTPException(409, f"Slug '{new_slug}' already exists")
            post.slug = new_slug
    if body.status is not None:
        if body.status not in ("draft", "published"):
            raise HTTPException(400, "status must be 'draft' or 'published'")
        post.status = body.status
    if body.title    is not None: post.title   = body.title.strip() or post.title
    if body.excerpt  is not None: post.excerpt = body.excerpt
    if body.content  is not None: post.content = body.content
    if body.tag      is not None: post.tag     = body.tag
    if body.image_url is not None: post.image_url = body.image_url or None

    session.commit()
    session.refresh(post)
    return _serialize(post)


@blog_router.delete("/{post_id}", status_code=204)
async def delete_post(
    post_id: str,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _admin(user)
    try:
        pid = uuid.UUID(post_id)
    except ValueError:
        raise HTTPException(400, "Invalid post ID")
    post = session.query(BlogPost).filter(BlogPost.id == pid).first()
    if not post:
        raise HTTPException(404, "Post not found")
    session.delete(post)
    session.commit()


@blog_router.post("/upload-image")
async def upload_blog_image(
    file: UploadFile = File(...),
    user=Depends(JWTBearer()),
):
    """Upload a blog hero image to Cloudinary; returns {url}."""
    _admin(user)
    ALLOWED = {"png", "jpg", "jpeg", "webp", "gif"}
    filename = file.filename or "upload"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED:
        raise HTTPException(400, f"Unsupported type '{ext}'. Allowed: {', '.join(ALLOWED)}")
    contents = await file.read()
    if len(contents) == 0:
        raise HTTPException(400, "File is empty")
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(400, "Image exceeds 10 MB")
    try:
        result = cloudinary.uploader.upload(contents, folder="littleloot/blog", resource_type="image")
    except Exception as exc:
        raise HTTPException(500, f"Cloudinary upload failed: {exc}")
    return {"url": result["secure_url"], "public_id": result["public_id"]}