# app/blog/models.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, DateTime
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base


class BlogPost(Base):
    __tablename__ = "blog_posts"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title      = Column(String(255), nullable=False)
    slug       = Column(String(255), nullable=False, unique=True, index=True)
    excerpt    = Column(Text, default="")
    content    = Column(Text, default="")
    tag        = Column(String(80), default="")
    image_url  = Column(String(600), nullable=True)
    status     = Column(String(20), nullable=False, default="draft")  # 'draft' | 'published'
    views      = Column(Integer, nullable=False, default=0)
    comments   = Column(Integer, nullable=False, default=0)
    likes      = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)