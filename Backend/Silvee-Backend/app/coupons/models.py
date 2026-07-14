# app/coupons/models.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base


class Coupon(Base):
    __tablename__ = "coupons"

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code           = Column(String(50), unique=True, nullable=False, index=True)
    discount_type  = Column(String(20), nullable=False)   # 'percentage' | 'flat'
    discount_value = Column(Float, nullable=False)
    min_order      = Column(Float, default=0)
    max_uses       = Column(Integer, default=100)
    used_count     = Column(Integer, default=0)
    is_active      = Column(Boolean, default=True)
    expires_at     = Column(DateTime(timezone=True), nullable=True)
    created_at     = Column(DateTime(timezone=True), default=datetime.utcnow)