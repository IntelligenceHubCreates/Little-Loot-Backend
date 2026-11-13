# app/reels/models.py
import uuid

from sqlalchemy import Boolean, Column, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.sql.sqltypes import TIMESTAMP

from app.models import Base


class InstagramReel(Base):
    """An Instagram reel/post shown on the home Reel Wall and/or linked products' pages."""
    __tablename__ = "instagram_reels"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    url          = Column(Text, nullable=False)                 # canonical https://www.instagram.com/reel/<code>/
    shortcode    = Column(String(40), nullable=False, unique=True)
    kind         = Column(String(10), nullable=False, default="reel")   # reel | p (post) | tv
    caption      = Column(String(200), nullable=True)
    cover_url    = Column(Text, nullable=True)                  # optional cover image (Cloudinary)
    product_ids  = Column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list)
    show_on_home = Column(Boolean, nullable=False, default=True)
    is_active    = Column(Boolean, nullable=False, default=True)
    sort_order   = Column(Integer, nullable=False, default=0)
    created_at   = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at   = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
