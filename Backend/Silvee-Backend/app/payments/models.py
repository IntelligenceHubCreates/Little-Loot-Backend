import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSON

from app.models import Base


class PaymentOrder(Base):
    __tablename__ = "payment_orders"

    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id             = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    razorpay_order_id   = Column(String(100), unique=True, nullable=False, index=True)
    razorpay_payment_id = Column(String(100), nullable=True, index=True)
    razorpay_signature  = Column(String(300), nullable=True)
    amount              = Column(Integer, nullable=False)   # paise
    currency            = Column(String(10), default="INR")
    status              = Column(String(30), default="created")
    cart_snapshot       = Column(JSON, nullable=True)
    shipping_address    = Column(JSON, nullable=True)
    is_verified         = Column(Boolean, default=False)
    created_at          = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    paid_at             = Column(DateTime(timezone=True), nullable=True)
