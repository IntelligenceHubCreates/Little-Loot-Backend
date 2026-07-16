# app/products/models.py
"""
FIX: Added sub_category_slug + sub_category_name columns to Product ORM.
     These are backfilled from the categories table so the frontend
     can display the correct sub-category label without an extra JOIN.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, validator
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID, ARRAY
from sqlalchemy.orm import Session, relationship

from app.db import Base


# ─── ORM: Category ────────────────────────────────────────────────

class Category(Base):
    __tablename__ = "categories"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name        = Column(String(120), nullable=False)
    slug        = Column(String(120), nullable=False, unique=True, index=True)
    parent_id   = Column(UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    emoji       = Column(String(10))
    description = Column(Text)
    sort_order  = Column(Integer, nullable=False, default=0)
    is_active   = Column(Boolean, nullable=False, default=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    parent   = relationship("Category", remote_side="Category.id", back_populates="children")
    children = relationship("Category", back_populates="parent", order_by="Category.sort_order")
    products = relationship("Product", back_populates="category_ref", lazy="dynamic")

    @property
    def is_root(self) -> bool:
        return self.parent_id is None

    # ── FIX: Use a single recursive SQL CTE instead of N+1 Python loop ──
    @staticmethod
    def get_descendant_ids(slug: str, session: Session) -> list[uuid.UUID]:
        """
        Returns the root category id + all descendant ids in ONE SQL query.
        Uses the get_category_ids() PostgreSQL function defined in subcategory_fix.sql.
        """
        rows = session.execute(
            "SELECT category_id FROM get_category_ids(:slug)",
            {"slug": slug}
        ).fetchall()
        return [row[0] for row in rows]


# ─── ORM: Product ─────────────────────────────────────────────────

class Product(Base):
    __tablename__ = "products"

    id                    = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name                  = Column(String(255), nullable=False)
    category_id           = Column(UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True)
    category              = Column(String(120))          # legacy plain-text
    # FIX: new columns - denormalised for fast display + filtering
    sub_category_slug     = Column(String(120), index=True)   # e.g. "puzzles"
    sub_category_name     = Column(String(120))               # e.g. "Puzzles"
    description           = Column(Text)
    details               = Column(ARRAY(String), nullable=False, default=list)
    original_price        = Column(Integer, nullable=False)
    amount_discount       = Column(Integer, nullable=False, default=0)
    percentage_discount   = Column(Integer, nullable=False, default=0)
    count                 = Column(Integer, nullable=False, default=0)
    product_image         = Column(JSON, nullable=False, default=list)
    brand                 = Column(String(120))
    age_range             = Column(String(40))
    is_new                = Column(Boolean, nullable=False, default=False)
    is_featured           = Column(Boolean, nullable=False, default=False)
    is_active             = Column(Boolean, nullable=False, default=True)
    offer_expiration_date = Column(DateTime(timezone=True))
    created_at            = Column(DateTime(timezone=True), server_default=func.now())
    updated_at            = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    variant_group_id      = Column(String(64), nullable=True, index=True)
    color                 = Column(String(50), nullable=True)   # e.g. "Pink"
    color_hex             = Column(String(7),  nullable=True)   # e.g. "#F4A7B9"
    color_variants        = Column(JSON, nullable=False, default=list)
    product_video         = Column(String(512), nullable=True)

    # ─── Relationships ─────────────────────────────────────────────
    category_ref = relationship("Category", back_populates="products")
    order_items  = relationship("OrderItem", back_populates="product")
    cart_items   = relationship("CartItem", back_populates="product")
    favorites    = relationship("Favorite", back_populates="product")
    ratings      = relationship("Rating", back_populates="product")


# ─── Pydantic: Category ───────────────────────────────────────────

class CategoryBase(BaseModel):
    id:          str
    name:        str
    slug:        str
    parent_id:   Optional[str]
    emoji:       Optional[str]
    description: Optional[str]
    sort_order:  int
    is_active:   bool

    class Config:
        from_attributes = True

    @validator("id", "parent_id", pre=True)
    def uuid_to_str(cls, v):
        return str(v) if v else None


class CategoryWithChildren(CategoryBase):
    children: List["CategoryWithChildren"] = []

CategoryWithChildren.model_rebuild()


# ─── Pydantic: Product ────────────────────────────────────────────

class ProductBase(BaseModel):
    id:                   str
    name:                 str
    category_id:          Optional[str]
    category:             Optional[str]
    # FIX: expose sub-category slug + name so frontend can filter/display correctly
    sub_category_slug:    Optional[str]
    sub_category_name:    Optional[str]
    variant_group_id:     Optional[str] = None
    color:                Optional[str] = None
    color_hex:            Optional[str] = None
    color_variants:       List[Any]     = []
    product_video:        Optional[str] = None
    description:          Optional[str]
    details:              List[Any]    = []
    original_price:       int
    amount_discount:      int          = 0
    percentage_discount:  int          = 0
    count:                int          = 0
    product_image:        List[Any]    = []
    brand:                Optional[str]
    age_range:            Optional[str]
    is_new:               bool         = False
    is_featured:          bool         = False
    is_active:            bool         = True
    offer_expiration_date: Optional[datetime]
    created_at:           Optional[datetime]
    average_rating:       float        = 0.0
    review_count:         int          = 0

    class Config:
        from_attributes = True

    @validator("id", "category_id", pre=True)
    def uuid_to_str(cls, v):
        return str(v) if v else None


class ProductListResponse(BaseModel):
    data:       List[ProductBase]
    totalCount: int
    page:       int = 1
    limit:      int = 20

class ProductIn(BaseModel):
    productName:            str
    productCategory:        str
    productDescription:     str
    productPrice:           int
    productCount:           int
    productDiscount:        int
    productDiscountAmount:  int
    productImages:          list
    productDetails:         list

    class Config:
        from_attributes = True