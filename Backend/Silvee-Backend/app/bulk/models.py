# app/bulk/models.py
"""
Bulk order enquiries (quote requests for 25+ units).

A customer asks for a quantity of a product (or describes what they need);
the store replies with a price by phone/WhatsApp. Uses the shared Base so the
product/user foreign keys resolve.
"""
import uuid

from sqlalchemy import Column, Date, DECIMAL, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql.sqltypes import TIMESTAMP

from app.models import Base

BULK_MIN_QUANTITY = 25
BULK_MAX_QUANTITY = 100000
BULK_STATUSES = ("new", "contacted", "quoted", "won", "lost")


class BulkEnquiry(Base):
    __tablename__ = "bulk_enquiries"

    id      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    # Product is optional ("I need 100 return gifts under ₹99"); snapshots keep the
    # enquiry readable even if the product is renamed or removed later.
    product_id   = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    product_name = Column(String(255), nullable=True)
    product_sku  = Column(String(20),  nullable=True)
    user_id      = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    quantity     = Column(Integer, nullable=False)
    name         = Column(String(120), nullable=False)
    phone        = Column(String(20),  nullable=False)
    email        = Column(String(255), nullable=True)
    city         = Column(String(120), nullable=True)
    needed_by    = Column(Date, nullable=True)
    personalise  = Column(String(10), nullable=True)      # "yes" | "no" | None (not asked)
    notes        = Column(Text, nullable=True)

    status        = Column(String(20), nullable=False, default="new", index=True)
    quoted_price  = Column(DECIMAL(20, 2), nullable=True)  # per-unit price offered
    admin_notes   = Column(Text, nullable=True)

    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
