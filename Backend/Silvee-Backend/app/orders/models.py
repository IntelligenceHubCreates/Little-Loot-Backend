# app/orders/models.py
from datetime import datetime
from app.models import Base
from sqlalchemy import Column, ForeignKey, Integer, String, DECIMAL, DateTime, Text, func
from sqlalchemy.sql.sqltypes import TIMESTAMP
from sqlalchemy.orm import relationship
import uuid
from sqlalchemy.dialects.postgresql import UUID
from app.users.models import Users
from app.products.models import Product


class Order(Base):
    __tablename__ = "orders"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id          = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    order_date       = Column(DateTime, default=datetime.utcnow)
    total_amount     = Column(DECIMAL(20, 2), nullable=False)
    status           = Column(String(50), nullable=False)
    shipping_address = Column(String(500), nullable=False)
    created_at       = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at       = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    razorpay_payment_id = Column(String(100), nullable=True, index=True)
    delivered_at        = Column(TIMESTAMP(timezone=True), nullable=True)
    user        = relationship("Users",     back_populates="orders")
    order_items = relationship("OrderItem", back_populates="order")
    # ── Order breakdown (NEW) — so the stored total is auditable ──────
    subtotal        = Column(DECIMAL(20, 2), nullable=True)          # sum of discounted line prices
    discount_amount = Column(DECIMAL(20, 2), nullable=True, default=0)  # coupon discount applied
    delivery_fee    = Column(DECIMAL(20, 2), nullable=True, default=0)
    coupon_code     = Column(String(50),     nullable=True)          # which coupon was used
    gift_message = Column(String(500), nullable=True)

class OrderItem(Base):
    __tablename__ = "order_items"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    order_id   = Column(UUID(as_uuid=True), ForeignKey("orders.id"),   nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    quantity   = Column(Integer,            nullable=False)
    price      = Column(DECIMAL(20, 2),     nullable=False)

    # ── Color variant fields (NEW) ─────────────────────────────────
    # Copied from the cart item at checkout so the owner always
    # knows which color variant the customer ordered.
    color      = Column(String(50), nullable=True)   # e.g. "Pink"
    color_hex  = Column(String(7),  nullable=True)   # e.g. "#F4A7B9"
    image      = Column(Text,       nullable=True)   # color-specific image URL

    order   = relationship("Order",   back_populates="order_items")
    product = relationship("Product", back_populates="order_items")