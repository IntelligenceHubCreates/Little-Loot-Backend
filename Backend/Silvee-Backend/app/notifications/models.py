# app/notifications/models.py
"""
Notification system (Phase 14, Stage 2a) — chosen scope beyond spec #14.

Two tables:
- Notification: a real per-user, customer-facing notification row. The account
  bell reads these (replacing the previous client-side-generated list). Generic
  by design (type/title/body/link/meta) so order, shipping, returns, offers all
  use it — not shipping-only.
- ShipmentNotificationLog: an audit row per (event, channel) dispatch attempt.
  Proves what fired and to which channel, and carries the WhatsApp/email send
  status. This is what the WhatsApp adapter writes to.

Conventions: shared app.models.Base, UUID PKs app-supplied, TIMESTAMPTZ.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Column, String, Boolean, Text, ForeignKey, func
from sqlalchemy.sql.sqltypes import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.models import Base


class Notification(Base):
    __tablename__ = "notifications"

    id      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    # Generic, multi-domain. type drives the icon/colour client-side.
    # e.g. 'order' | 'shipping' | 'return' | 'offer' | 'system'
    type   = Column(String(40), nullable=False, default="system", index=True)
    title  = Column(String(200), nullable=False)
    body   = Column(Text, nullable=True)

    # Optional deep-link target the bell can route to (e.g. '/track-order?order_id=…').
    link   = Column(Text, nullable=True)

    # Free-form structured payload (order_id, shipment_id, awb, etc.) — never
    # shown raw; the client can use it to build links/badges.
    meta   = Column(JSONB, nullable=True)

    is_read    = Column(Boolean, nullable=False, default=False, index=True)
    read_at    = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), index=True)


class ShipmentNotificationLog(Base):
    __tablename__ = "shipment_notification_logs"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    shipment_id = Column(UUID(as_uuid=True), ForeignKey("shipments.id"), nullable=True, index=True)
    user_id     = Column(UUID(as_uuid=True), ForeignKey("users.id"),     nullable=True)

    event   = Column(String(60), nullable=False)   # 'shipped' | 'delivered' | 'delivery_failed' | …
    channel = Column(String(20), nullable=False)   # 'in_app' | 'whatsapp' | 'email' | 'sms'

    # 'sent' | 'skipped_no_provider' | 'failed' | 'logged'
    status        = Column(String(30), nullable=False, default="logged")
    provider      = Column(String(40), nullable=True)   # 'aisensy' | 'smtp' | …
    provider_ref  = Column(String(200), nullable=True)  # provider message id once live
    error         = Column(Text, nullable=True)
    payload       = Column(JSONB, nullable=True)         # exact payload built (recipient/template/vars)

    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())