# app/returns/models.py
"""
Return & Damaged Product Management — data models (Phase 13, Stage 2).

Six tables: return_requests, return_items, return_proofs,
return_status_history, refunds, replacement_shipments.

IMPORTANT — base choice:
    These models use the SAME declarative Base as Order/OrderItem/Product/Users
    (app.models.Base). SQLAlchemy resolves relationship() targets and FK strings
    against a single base's registry; since these tables FK into orders,
    order_items, products and users, they must share that registry.

Money columns are DECIMAL(20, 2) to match Order.total_amount and OrderItem.price
exactly (mixing Float and DECIMAL causes rounding drift on refund maths).

Timestamps are TIMESTAMP(timezone=True) to match the orders table, so the
Stage-3 eligibility window can compare against a tz-aware "now" without the
naive/aware TypeError seen in Phase 11.

Status / type / reason / condition are stored as plain VARCHAR (NOT Postgres
ENUMs) and validated in the Pydantic layer (Stage 3). This matches the existing
free-text Order.status and keeps the value sets easy to evolve (e.g. adding
store_credit later needs no ALTER TYPE).
"""
import uuid

from sqlalchemy import (
    Column, String, Text, Integer, DECIMAL, Boolean, ForeignKey, func,
)
from sqlalchemy.sql.sqltypes import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.models import Base  # SAME base as Order / OrderItem / Product / Users


# ── Valid value sets (enforced in Pydantic in Stage 3; here for reference) ──
RETURN_STATUSES = (
    "requested", "under_review", "approved", "rejected",
    "pickup_scheduled", "picked_up", "received",
    "replacement_dispatched", "refunded", "completed", "cancelled_by_customer",
)
REQUEST_TYPES = ("replacement", "refund", "store_credit")
RETURN_REASONS = ("damaged", "wrong_item", "missing_item", "defective", "variant_issue", "other")
ITEM_CONDITIONS = ("pending", "resellable", "damaged")
REFUND_STATUSES = ("pending", "initiated", "completed", "failed")
REPLACEMENT_STATUSES = ("pending", "dispatched", "delivered")


class ReturnRequest(Base):
    __tablename__ = "return_requests"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    # NO ondelete on order_id by design: an order that has return/refund records
    # cannot be hard-deleted (protects financial audit). See note in the answer.
    order_id     = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, index=True)
    user_id      = Column(UUID(as_uuid=True), ForeignKey("users.id"),  nullable=False, index=True)

    status       = Column(String(40), nullable=False, default="requested", index=True)
    request_type = Column(String(20), nullable=False)   # replacement | refund | store_credit
    reason       = Column(String(30), nullable=False)   # damaged | wrong_item | missing_item | defective | variant_issue | other
    description  = Column(Text, nullable=True)

    total_refund_amount = Column(DECIMAL(20, 2), nullable=True)   # set when a refund is decided
    admin_notes         = Column(Text, nullable=True)
    rejection_reason    = Column(Text, nullable=True)

    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    # Owned children — cascade delete (both ORM-side and DB-side via ondelete).
    items          = relationship("ReturnItem",          back_populates="return_request", cascade="all, delete-orphan")
    proofs         = relationship("ReturnProof",         back_populates="return_request", cascade="all, delete-orphan")
    status_history = relationship("ReturnStatusHistory", back_populates="return_request", cascade="all, delete-orphan",
                                  order_by="ReturnStatusHistory.created_at")
    refund         = relationship("Refund",              back_populates="return_request", cascade="all, delete-orphan", uselist=False)
    replacement    = relationship("ReplacementShipment", back_populates="return_request", cascade="all, delete-orphan", uselist=False)


class ReturnItem(Base):
    __tablename__ = "return_items"

    id                = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    return_request_id = Column(UUID(as_uuid=True), ForeignKey("return_requests.id", ondelete="CASCADE"), nullable=False, index=True)
    order_item_id     = Column(UUID(as_uuid=True), ForeignKey("order_items.id"), nullable=False)
    product_id        = Column(UUID(as_uuid=True), ForeignKey("products.id"),    nullable=False)

    quantity   = Column(Integer, nullable=False)
    # Snapshot of OrderItem.price at request time — refund maths must use what the
    # customer PAID, not the product's current price (which may have changed).
    item_price = Column(DECIMAL(20, 2), nullable=False)

    condition_status = Column(String(20), nullable=False, default="pending")  # pending | resellable | damaged (set at receive)
    restock_quantity = Column(Integer,    nullable=False, default=0)           # units actually returned to sellable stock
    is_resellable    = Column(Boolean,    nullable=False, default=False)

    return_request = relationship("ReturnRequest", back_populates="items")


class ReturnProof(Base):
    __tablename__ = "return_proofs"

    id                = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    return_request_id = Column(UUID(as_uuid=True), ForeignKey("return_requests.id", ondelete="CASCADE"), nullable=False, index=True)
    file_url          = Column(Text,        nullable=False)
    file_type         = Column(String(20),  nullable=False, default="image")  # image | video
    public_id         = Column(String(255), nullable=True)                    # Cloudinary public_id (for later deletion)
    created_at        = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    return_request = relationship("ReturnRequest", back_populates="proofs")


class ReturnStatusHistory(Base):
    __tablename__ = "return_status_history"

    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    return_request_id   = Column(UUID(as_uuid=True), ForeignKey("return_requests.id", ondelete="CASCADE"), nullable=False, index=True)
    old_status          = Column(String(40), nullable=True)
    new_status          = Column(String(40), nullable=False)
    # Who made the change. Admin for most transitions; may be the customer (for
    # cancellation) or NULL for system actions. SET NULL keeps the audit row if
    # the user account is ever removed.
    changed_by_admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    note                = Column(Text, nullable=True)
    created_at          = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    return_request = relationship("ReturnRequest", back_populates="status_history")


class Refund(Base):
    __tablename__ = "refunds"

    id                    = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    # UNIQUE → at most one refund per return request (1:1). Stage-3 service is
    # idempotent (find-or-create) so a double-click can't violate this.
    return_request_id     = Column(UUID(as_uuid=True), ForeignKey("return_requests.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    amount                = Column(DECIMAL(20, 2), nullable=False)
    method                = Column(String(30), nullable=False, default="manual")   # manual | razorpay | upi | bank_transfer | store_credit
    status                = Column(String(20), nullable=False, default="pending")  # pending | initiated | completed | failed
    transaction_reference = Column(String(255), nullable=True)
    processed_at          = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at            = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at            = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    # ── Razorpay gateway tracking (Phase 13 refunds) ──
    gateway_refund_id    = Column(String(64), nullable=True, index=True)   # rfnd_… — webhook & reconciliation key
    gateway_payment_id   = Column(String(64), nullable=True)               # pay_… that was refunded (audit)
    gateway_status       = Column(String(20), nullable=True)               # Razorpay-native: pending|processed|failed
    speed                = Column(String(12), nullable=True)               # normal|optimum (what was requested)

    return_request = relationship("ReturnRequest", back_populates="refund")


class ReplacementShipment(Base):
    __tablename__ = "replacement_shipments"

    id                = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    return_request_id = Column(UUID(as_uuid=True), ForeignKey("return_requests.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    product_id        = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    quantity          = Column(Integer, nullable=False, default=1)
    status            = Column(String(20), nullable=False, default="pending")  # pending | dispatched | delivered
    tracking_number   = Column(String(120), nullable=True)
    dispatched_at     = Column(TIMESTAMP(timezone=True), nullable=True)
    delivered_at      = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at        = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at        = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    return_request = relationship("ReturnRequest", back_populates="replacement")