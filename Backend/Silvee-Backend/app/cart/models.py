# app/cart/models.py
from datetime import datetime
from sqlalchemy import Column, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship
from sqlalchemy.sql.sqltypes import TIMESTAMP
import uuid
from sqlalchemy.dialects.postgresql import UUID
from app.models import Base

class Cart(Base):
    __tablename__ = "carts"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), onupdate=func.now())

    user       = relationship("Users", back_populates="cart")
    cart_items = relationship("CartItem", back_populates="cart", cascade="all, delete-orphan")


class CartItem(Base):
    __tablename__ = "cart_items"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    cart_id    = Column(UUID(as_uuid=True), ForeignKey("carts.id"),    nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    quantity   = Column(Integer, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), onupdate=func.now())

    # ── Color variant fields ───────────────────────────────────────
    # Saved when user adds a product with a specific color selected.
    # Lets the owner know exactly which color was ordered.
    color      = Column(String(50), nullable=True)   # e.g. "Pink"
    color_hex  = Column(String(7),  nullable=True)   # e.g. "#F4A7B9"
    image      = Column(Text,       nullable=True)   # color-specific image URL

    cart    = relationship("Cart",    back_populates="cart_items")
    product = relationship("Product", back_populates="cart_items")