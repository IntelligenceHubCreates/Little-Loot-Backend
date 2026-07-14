# app/shipping/models.py
"""
Shipping & Logistics models (Phase 14, Stage 1).

Same conventions as the returns subsystem:
- Shared app.models.Base (one metadata) so create_all sees these tables.
- UUID PKs, app-supplied (no DB default), matching orders/returns.
- DECIMAL(20,2) for money; TIMESTAMP(timezone=True) for all timestamps.
- Status/reason/condition are VARCHAR, validated in Pydantic (Stage 2), NOT DB
  enums — mirrors returns, keeps migrations painless.
- FK to orders.id has NO cascade: a shipment + its COD/label history is an audit
  record. (Consequence: deleting an order with a shipment will 500 until a 409
  guard is added — identical known edge to returns, same optional fix later.)

Shipment is 1:N from Order (partial-shipment-capable per business rule #19); the
Stage-3 create flow defaults to ONE shipment containing all order items.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Integer, Boolean, Text, Date,
    DECIMAL, ForeignKey, func,
)
from sqlalchemy.sql.sqltypes import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.models import Base


class Shipment(Base):
    __tablename__ = "shipments"

    id       = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, index=True)
    user_id  = Column(UUID(as_uuid=True), ForeignKey("users.id"),  nullable=True,  index=True)

    # Lifecycle status (state machine validated in Stage-2 services)
    status = Column(String(40), nullable=False, default="pending", index=True)

    # Payment context — derived from Order.razorpay_payment_id (no payment_method
    # column exists). is_prepaid True => paid via Razorpay; False => COD/unpaid.
    is_prepaid = Column(Boolean, nullable=False, default=False)

    # COD tracking (only meaningful when is_prepaid is False)
    cod_amount               = Column(DECIMAL(20, 2), nullable=True)
    cod_collected            = Column(Boolean, nullable=False, default=False)
    cod_collected_at         = Column(TIMESTAMP(timezone=True), nullable=True)
    cod_remitted             = Column(Boolean, nullable=False, default=False)
    cod_remittance_reference = Column(String(120), nullable=True)
    cod_remitted_at          = Column(TIMESTAMP(timezone=True), nullable=True)

    # Courier assignment (manual today; Shiprocket API fills these later)
    courier_partner_id = Column(UUID(as_uuid=True), ForeignKey("courier_partners.id"), nullable=True)
    courier_name       = Column(String(120), nullable=True)
    courier_service    = Column(String(120), nullable=True)
    awb_number         = Column(String(120), nullable=True, index=True)
    tracking_url       = Column(Text, nullable=True)

    # Label (manual upload to Cloudinary today; auto-generated later)
    label_url          = Column(Text, nullable=True)
    label_public_id    = Column(String(200), nullable=True)
    label_generated_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Logistics figures
    shipping_cost  = Column(DECIMAL(20, 2), nullable=True)
    package_weight = Column(DECIMAL(20, 2), nullable=True)  # kg
    package_length = Column(DECIMAL(20, 2), nullable=True)  # cm
    package_width  = Column(DECIMAL(20, 2), nullable=True)
    package_height = Column(DECIMAL(20, 2), nullable=True)

    # Denormalized shipping address — parsed from the order's flattened
    # shipping_address string at creation, then admin-editable. (No Address table
    # exists; the order stores only "name, line1, city, state - pincode".)
    ship_name    = Column(String(200), nullable=True)
    ship_phone   = Column(String(20),  nullable=True)
    ship_line1   = Column(String(300), nullable=True)
    ship_city    = Column(String(120), nullable=True, index=True)
    ship_state   = Column(String(120), nullable=True, index=True)
    ship_pincode = Column(String(12),  nullable=True, index=True)

    # Milestone timestamps
    expected_delivery_date = Column(Date, nullable=True)
    pickup_scheduled_at = Column(TIMESTAMP(timezone=True), nullable=True)   # ADD
    pickup_attempts     = Column(Integer, nullable=False, default=0)        # ADD
    packed_at        = Column(TIMESTAMP(timezone=True), nullable=True)
    picked_up_at     = Column(TIMESTAMP(timezone=True), nullable=True)
    delivered_at     = Column(TIMESTAMP(timezone=True), nullable=True)
    rto_initiated_at = Column(TIMESTAMP(timezone=True), nullable=True)
    rto_received_at  = Column(TIMESTAMP(timezone=True), nullable=True)

    admin_notes = Column(Text, nullable=True)

    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    # Relationships
    items          = relationship("ShipmentItem",          back_populates="shipment", cascade="all, delete-orphan")
    status_history = relationship("ShipmentStatusHistory", back_populates="shipment", cascade="all, delete-orphan")
    attempts       = relationship("DeliveryAttempt",       back_populates="shipment", cascade="all, delete-orphan")
    labels         = relationship("ShippingLabel",         back_populates="shipment", cascade="all, delete-orphan")
    courier        = relationship("CourierPartner")


class ShipmentItem(Base):
    __tablename__ = "shipment_items"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    shipment_id   = Column(UUID(as_uuid=True), ForeignKey("shipments.id"),    nullable=False, index=True)
    order_item_id = Column(UUID(as_uuid=True), ForeignKey("order_items.id"),  nullable=False)
    product_id    = Column(UUID(as_uuid=True), ForeignKey("products.id"),     nullable=False)
    quantity      = Column(Integer, nullable=False)
    condition_status = Column(String(20), nullable=False, default="pending")  # pending|resellable|damaged
    restock_quantity = Column(Integer,    nullable=False, default=0)
    is_resellable    = Column(Boolean,    nullable=False, default=False)       # ADD all three

    shipment = relationship("Shipment", back_populates="items")


class ShipmentStatusHistory(Base):
    __tablename__ = "shipment_status_history"

    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    shipment_id         = Column(UUID(as_uuid=True), ForeignKey("shipments.id"), nullable=False, index=True)
    old_status          = Column(String(40), nullable=True)
    new_status          = Column(String(40), nullable=False)
    changed_by_admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    note                = Column(Text, nullable=True)
    created_at          = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    shipment = relationship("Shipment", back_populates="status_history")


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"

    id                 = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    shipment_id        = Column(UUID(as_uuid=True), ForeignKey("shipments.id"), nullable=False, index=True)
    attempt_number     = Column(Integer, nullable=False, default=1)
    attempted_at       = Column(TIMESTAMP(timezone=True), nullable=True)
    status             = Column(String(40), nullable=False)  # attempted | failed | delivered
    failure_reason     = Column(String(60), nullable=True)
    courier_remarks    = Column(Text, nullable=True)
    next_attempt_at    = Column(TIMESTAMP(timezone=True), nullable=True)
    customer_contacted = Column(Boolean, nullable=False, default=False)
    created_at         = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    shipment = relationship("Shipment", back_populates="attempts")


class CourierPartner(Base):
    __tablename__ = "courier_partners"

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name                 = Column(String(120), nullable=False)
    service_type         = Column(String(120), nullable=True)
    is_active            = Column(Boolean, nullable=False, default=True)
    supports_cod         = Column(Boolean, nullable=False, default=True)
    # {awb} placeholder is substituted to build a Shipment.tracking_url.
    tracking_url_template = Column(Text, nullable=True)
    created_at           = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at           = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ShippingLabel(Base):
    __tablename__ = "shipping_labels"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    shipment_id     = Column(UUID(as_uuid=True), ForeignKey("shipments.id"), nullable=False, index=True)
    label_url       = Column(Text, nullable=False)
    label_public_id = Column(String(200), nullable=True)
    file_name       = Column(String(200), nullable=True)
    generated_by    = Column(String(40), nullable=True)  # 'manual' | 'shiprocket' | ...
    created_at      = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    shipment = relationship("Shipment", back_populates="labels")