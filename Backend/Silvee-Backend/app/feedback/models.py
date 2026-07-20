import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base


class CustomerFeedback(Base):
    __tablename__ = "customer_feedback"

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_name  = Column(String(120), nullable=False)
    image_url      = Column(String(600), nullable=True)
    video_url      = Column(String(600), nullable=True)
    thumbnail_url  = Column(String(600), nullable=True)
    caption        = Column(Text, nullable=True)
    is_active      = Column(Boolean, nullable=False, default=True)
    display_order  = Column(Integer, nullable=False, default=0)
    created_at     = Column(DateTime(timezone=True), default=datetime.utcnow)
