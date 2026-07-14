# app/shipping/services.py
"""
Shipping & Logistics — business logic (Phase 14, Stage 2b).

Owns the shipment state machine (+ history), order-status sync, customer
notifications, and the RTO restock side-effect. Same architecture as returns:
TRANSITIONS dict, _transition() validates+writes history (no-op if already in
target → idempotent), hand-built serializers, tz-aware UTC, Decimal money.

KEY DESIGN: _transition centralizes three things on every real status change:
  1. status-history row
  2. order-status sync via orders.services.apply_order_status_sync (Stage 1)
  3. customer notification via notifications.dispatcher.emit_notification (2a)
So each operation just drives the state machine; sync + notify happen for free,
exactly once (a no-op transition fires nothing).

MANUAL MODE: courier/AWB/label are admin-entered. The Shiprocket adapter is NOT
called here — that's the later 'activate automation' pass. WhatsApp logs-and-
skips via the dispatcher until provider creds exist.
"""
from __future__ import annotations

import datetime
import re
from datetime import timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import String, cast, or_
from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session, joinedload, selectinload

from app.shipping.models import (
    Shipment, ShipmentItem, ShipmentStatusHistory,
    DeliveryAttempt, CourierPartner, ShippingLabel,
)
from app.orders.models import Order, OrderItem
from app.orders.services import apply_order_status_sync
from app.products.models import Product
from app.users.models import Users
from app.notifications.dispatcher import emit_notification


# ═══════════════════════════════════════════════════════════════════
# State machine
# ═══════════════════════════════════════════════════════════════════



TRANSITIONS: Dict[str, set] = {
    "pending":            {"ready_to_pack", "cancelled"},
    "ready_to_pack":      {"packed", "cancelled"},
    "packed":             {"label_generated", "pickup_scheduled", "cancelled"},
    "label_generated":    {"pickup_scheduled", "picked_up", "cancelled"},
    "pickup_scheduled":   {"picked_up", "cancelled"},
    "picked_up":          {"in_transit", "out_for_delivery", "delivered", "lost", "damaged_in_transit"},
    "in_transit":         {"out_for_delivery", "delivered", "delivery_failed", "lost", "damaged_in_transit", "rto_initiated"},
    "out_for_delivery":   {"delivered", "delivery_failed"},
    "delivery_failed":    {"out_for_delivery", "in_transit", "rto_initiated"},
    "rto_initiated":      {"returned_to_origin", "lost", "damaged_in_transit"},
    "returned_to_origin": set(),          # terminal (RTO received)
    "delivered":          set(),          # terminal — cannot cancel after delivered
    "lost":               set(),          # terminal
    "damaged_in_transit": {"rto_initiated", "returned_to_origin"},
    "cancelled":          set(),          # terminal
}

# Shipment status entered → order status to write (only these touch the order).
SHIPMENT_TO_ORDER = {
    "packed":             "packed",
    "picked_up":          "shipped",
    "in_transit":         "shipped",
    "out_for_delivery":   "out_for_delivery",
    "delivered":          "delivered",
    "cancelled":          "cancelled",
    "returned_to_origin": "returned",
}

# Shipment status entered → customer notification event (fires once on entry).
STATUS_NOTIFY = {
    "packed":           "packed",
    "picked_up":        "shipped",
    "out_for_delivery": "out_for_delivery",
    "delivered":        "delivered",
    "delivery_failed":  "delivery_failed",
    "rto_initiated":    "rto_initiated",
}


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

# Order statuses that count as "awaiting shipment" (post-checkout, not terminal,
# not already handed to shipping). Free-text Order.status is mixed-case, so we
# compare lowercased.
AWAITING_ORDER_STATUSES = {"pending", "confirmed", "processing", "packed"}
# Order statuses that mean shipping has already started / order is done.
_NOT_AWAITING = {"shipped", "out_for_delivery", "delivered", "cancelled", "returned"}


def list_orders_awaiting_shipment(db: Session, skip: int = 0, limit: int = 50) -> dict:
    """Confirmed/paid orders with no non-cancelled shipment. Newest first."""
    # order_ids that already have an active shipment
    active_ship_orders = (
        db.query(Shipment.order_id).filter(Shipment.status != "cancelled").distinct().subquery()
    )
    q = (
        db.query(Order)
        .options(selectinload(Order.order_items), joinedload(Order.user))
        .filter(~Order.id.in_(active_ship_orders))
        .filter(sqlfunc.lower(Order.status).in_(AWAITING_ORDER_STATUSES))
        .order_by(Order.created_at.desc())
    )
    total = q.count()
    rows = q.offset(skip).limit(limit).all()

    out = []
    for o in rows:
        u = o.user
        item_count = sum(int(oi.quantity or 0) for oi in (o.order_items or []))
        is_prepaid = o.razorpay_payment_id is not None
        out.append({
            "order_id": str(o.id),
            "order_number": str(o.id)[:8].upper(),
            "status": o.status,
            "total_amount": _money(o.total_amount),
            "item_count": item_count,
            "line_count": len(o.order_items or []),
            "is_prepaid": is_prepaid,
            "customer_name": (u.name or "") if u else "",
            "customer_phone": (u.phone or "") if u else "",
            "created_at": o.created_at.isoformat() if o.created_at else "",
        })
    return {"data": out, "totalCount": total, "page": (skip // limit) + 1 if limit else 1, "limit": limit}


def shipments_for_orders(db: Session, order_ids: List[str]) -> dict:
    """order_id → newest non-cancelled shipment id (or absent). For Orders-page badges."""
    if not order_ids:
        return {"data": {}}
    uuids = []
    for x in order_ids:
        try:
            uuids.append(UUID(str(x)))
        except (ValueError, TypeError):
            pass
    if not uuids:
        return {"data": {}}
    rows = (
        db.query(Shipment.order_id, Shipment.id, Shipment.status, Shipment.created_at)
        .filter(Shipment.order_id.in_(uuids), Shipment.status != "cancelled")
        .order_by(Shipment.created_at.desc())
        .all()
    )
    mapping: Dict[str, dict] = {}
    for order_id, sid, status, _created in rows:
        key = str(order_id)
        if key not in mapping:  # first = newest (ordered desc)
            mapping[key] = {"shipment_id": str(sid), "status": status}
    return {"data": mapping}

def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(timezone.utc)


def _ensure_aware(dt):
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _as_uuid(val: Any, what: str = "id") -> UUID:
    try:
        return UUID(str(val))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid {what}")


def _money(v) -> float:
    return float(v) if v is not None else 0.0


def _normalize_phone(phone) -> Optional[str]:
    """10-digit Indian → E.164-ish '91XXXXXXXXXX' for WhatsApp."""
    if not phone:
        return None
    d = "".join(ch for ch in str(phone) if ch.isdigit())
    if len(d) == 10:
        return "91" + d
    if len(d) >= 12 and d.startswith("91"):
        return d[-12:]
    return d or None


def _parse_shipping_address(addr: str) -> dict:
    """Best-effort parse of the order's flattened 'name, line1, city, state - pincode'
    string. Admin-editable afterwards (no Address table exists)."""
    addr = (addr or "").strip()
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    name  = parts[0] if parts else ""
    line1 = parts[1] if len(parts) > 1 else addr
    city  = parts[2] if len(parts) > 2 else (parts[-2] if len(parts) >= 2 else "")
    state, pincode = "", ""
    tail = parts[3] if len(parts) > 3 else (parts[-1] if "-" in addr else "")
    if "-" in tail:
        st, _, pin = tail.rpartition("-")
        state = st.strip()
        pincode = "".join(c for c in pin if c.isdigit())
    else:
        state = tail.strip()
    if not pincode:
        m = re.search(r"\b(\d{6})\b", addr)
        if m:
            pincode = m.group(1)
    return {"name": name, "line1": line1, "city": city, "state": state, "pincode": pincode}


def _record_history(db: Session, sh: Shipment, old, new, actor_id, note):
    db.add(ShipmentStatusHistory(
        shipment_id=sh.id, old_status=old, new_status=new,
        changed_by_admin_id=actor_id, note=note,
    ))


def _notify(db: Session, sh: Shipment, event: str):
    user = db.query(Users).filter(Users.id == sh.user_id).first() if sh.user_id else None
    emit_notification(
        db, event=event, user_id=sh.user_id, order_id=sh.order_id, shipment_id=sh.id,
        phone_e164=_normalize_phone(user.phone if user else None),
        courier_name=sh.courier_name or "", awb_number=sh.awb_number or "",
        tracking_url=sh.tracking_url or "",
    )


def _transition(db: Session, sh: Shipment, new_status: str,
                actor_id: Optional[UUID] = None, note: Optional[str] = None):
    """Validate + apply a status change; write history; sync order; notify.
    No-op (and no side-effects) if already in target — keeps ops idempotent."""
    old = sh.status
    if old == new_status:
        return
    if new_status not in TRANSITIONS.get(old, set()):
        raise HTTPException(status_code=409, detail=f"Cannot move shipment from '{old}' to '{new_status}'")

    sh.status = new_status
    _record_history(db, sh, old, new_status, actor_id, note)

    now = _utcnow()
    if new_status == "packed" and sh.packed_at is None:               sh.packed_at = now
    if new_status == "picked_up" and sh.picked_up_at is None:         sh.picked_up_at = now
    if new_status == "delivered" and sh.delivered_at is None:         sh.delivered_at = now
    if new_status == "rto_initiated" and sh.rto_initiated_at is None: sh.rto_initiated_at = now
    if new_status == "returned_to_origin" and sh.rto_received_at is None: sh.rto_received_at = now

    order_status = SHIPMENT_TO_ORDER.get(new_status)
    if order_status:
        apply_order_status_sync(db, sh.order_id, order_status)

    event = STATUS_NOTIFY.get(new_status)
    if event:
        _notify(db, sh, event)


def _build_tracking_url(db: Session, courier_partner_id, awb: str, provided: Optional[str]) -> Optional[str]:
    if provided:
        return provided
    if courier_partner_id and awb:
        cp = db.query(CourierPartner).filter(CourierPartner.id == courier_partner_id).first()
        if cp and cp.tracking_url_template:
            return cp.tracking_url_template.replace("{awb}", awb)
    return None


def _load(db: Session, shipment_id) -> Shipment:
    sh = (
        db.query(Shipment)
        .options(
            selectinload(Shipment.items),
            selectinload(Shipment.status_history),
            selectinload(Shipment.attempts),
            selectinload(Shipment.labels),
            joinedload(Shipment.courier),
        )
        .filter(Shipment.id == _as_uuid(shipment_id, "shipment_id"))
        .first()
    )
    if not sh:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return sh


# ═══════════════════════════════════════════════════════════════════
# Serializers
# ═══════════════════════════════════════════════════════════════════

def serialize_item(it: ShipmentItem) -> dict:
    return {
        "id": str(it.id), "order_item_id": str(it.order_item_id),
        "product_id": str(it.product_id), "quantity": int(it.quantity),
        "condition_status": it.condition_status, "restock_quantity": int(it.restock_quantity or 0),
        "is_resellable": bool(it.is_resellable),
    }


def serialize_history(h: ShipmentStatusHistory) -> dict:
    return {"id": str(h.id), "old_status": h.old_status, "new_status": h.new_status,
            "note": h.note, "created_at": h.created_at.isoformat() if h.created_at else ""}


def serialize_attempt(a: DeliveryAttempt) -> dict:
    return {
        "id": str(a.id), "attempt_number": int(a.attempt_number or 1),
        "attempted_at": a.attempted_at.isoformat() if a.attempted_at else None,
        "status": a.status, "failure_reason": a.failure_reason, "courier_remarks": a.courier_remarks,
        "next_attempt_at": a.next_attempt_at.isoformat() if a.next_attempt_at else None,
        "customer_contacted": bool(a.customer_contacted),
        "created_at": a.created_at.isoformat() if a.created_at else "",
    }


def serialize_shipment_full(db: Session, sh: Shipment, include_order: bool = True) -> dict:
    # enrich items with product name/image/stock
    pids = {it.product_id for it in sh.items if it.product_id}
    products: Dict[Any, dict] = {}
    if pids:
        for p in db.query(Product).filter(Product.id.in_(pids)).all():
            img = None
            pi = getattr(p, "product_image", None)
            if isinstance(pi, list) and pi:
                f = pi[0]
                img = f.get("url") if isinstance(f, dict) else (f if isinstance(f, str) else None)
            products[p.id] = {"name": p.name, "image": img, "current_stock": int(getattr(p, "count", 0) or 0)}

    items_out = []
    for it in sh.items:
        d = serialize_item(it)
        pinfo = products.get(it.product_id, {})
        d["product_name"]  = pinfo.get("name", "")
        d["product_image"] = pinfo.get("image")
        d["current_stock"] = pinfo.get("current_stock", 0)
        items_out.append(d)

    epoch = datetime.datetime.min.replace(tzinfo=timezone.utc)
    history = sorted(sh.status_history, key=lambda x: _ensure_aware(x.created_at) or epoch)
    attempts = sorted(sh.attempts, key=lambda x: _ensure_aware(x.created_at) or epoch)

    out = {
        "id": str(sh.id), "order_id": str(sh.order_id),
        "user_id": str(sh.user_id) if sh.user_id else None,
        "status": sh.status, "is_prepaid": bool(sh.is_prepaid),
        "cod_amount": (_money(sh.cod_amount) if sh.cod_amount is not None else None),
        "cod_collected": bool(sh.cod_collected), "cod_collected_at": sh.cod_collected_at.isoformat() if sh.cod_collected_at else None,
        "cod_remitted": bool(sh.cod_remitted), "cod_remittance_reference": sh.cod_remittance_reference,
        "cod_remitted_at": sh.cod_remitted_at.isoformat() if sh.cod_remitted_at else None,
        "courier_partner_id": str(sh.courier_partner_id) if sh.courier_partner_id else None,
        "courier_name": sh.courier_name, "courier_service": sh.courier_service,
        "awb_number": sh.awb_number, "tracking_url": sh.tracking_url,
        "label_url": sh.label_url, "label_generated_at": sh.label_generated_at.isoformat() if sh.label_generated_at else None,
        "shipping_cost": (_money(sh.shipping_cost) if sh.shipping_cost is not None else None),
        "package_weight": (_money(sh.package_weight) if sh.package_weight is not None else None),
        "package_length": (_money(sh.package_length) if sh.package_length is not None else None),
        "package_width":  (_money(sh.package_width) if sh.package_width is not None else None),
        "package_height": (_money(sh.package_height) if sh.package_height is not None else None),
        "ship_name": sh.ship_name, "ship_phone": sh.ship_phone, "ship_line1": sh.ship_line1,
        "ship_city": sh.ship_city, "ship_state": sh.ship_state, "ship_pincode": sh.ship_pincode,
        "expected_delivery_date": sh.expected_delivery_date.isoformat() if sh.expected_delivery_date else None,
        "pickup_scheduled_at": sh.pickup_scheduled_at.isoformat() if sh.pickup_scheduled_at else None,
        "pickup_attempts": int(sh.pickup_attempts or 0),
        "packed_at": sh.packed_at.isoformat() if sh.packed_at else None,
        "picked_up_at": sh.picked_up_at.isoformat() if sh.picked_up_at else None,
        "delivered_at": sh.delivered_at.isoformat() if sh.delivered_at else None,
        "rto_initiated_at": sh.rto_initiated_at.isoformat() if sh.rto_initiated_at else None,
        "rto_received_at": sh.rto_received_at.isoformat() if sh.rto_received_at else None,
        "admin_notes": sh.admin_notes,
        "created_at": sh.created_at.isoformat() if sh.created_at else "",
        "updated_at": sh.updated_at.isoformat() if sh.updated_at else "",
        "items": items_out,
        "status_history": [serialize_history(h) for h in history],
        "attempts": [serialize_attempt(a) for a in attempts],
        "labels": [{"id": str(l.id), "label_url": l.label_url, "file_name": l.file_name,
                    "generated_by": l.generated_by, "created_at": l.created_at.isoformat() if l.created_at else ""}
                   for l in sh.labels],
    }

    if include_order:
        order = db.query(Order).options(joinedload(Order.user)).filter(Order.id == sh.order_id).first()
        if order:
            u = order.user
            out["order"] = {
                "id": str(order.id), "order_number": str(order.id)[:8].upper(),
                "status": order.status, "total_amount": _money(order.total_amount),
                "razorpay_payment_id": getattr(order, "razorpay_payment_id", None),
                "created_at": order.created_at.isoformat() if order.created_at else "",
                "delivered_at": order.delivered_at.isoformat() if getattr(order, "delivered_at", None) else None,
            }
            out["customer"] = {
                "id": str(u.id) if u else "", "name": (u.name or "") if u else "",
                "email": (u.email or "") if u else "", "phone": (u.phone or "") if u else "",
            }
    return out


def serialize_tracking(db: Session, sh: Shipment) -> dict:
    """Customer-safe payload (NO admin_notes, NO costs). Used by the customer
    tracking endpoint in Stage 3/6."""
    epoch = datetime.datetime.min.replace(tzinfo=timezone.utc)
    history = sorted(sh.status_history, key=lambda x: _ensure_aware(x.created_at) or epoch)
    attempts = sorted(sh.attempts, key=lambda x: _ensure_aware(x.created_at) or epoch)
    return {
        "shipment_id": str(sh.id), "status": sh.status,
        "courier_name": sh.courier_name, "awb_number": sh.awb_number, "tracking_url": sh.tracking_url,
        "expected_delivery_date": sh.expected_delivery_date.isoformat() if sh.expected_delivery_date else None,
        "delivered_at": sh.delivered_at.isoformat() if sh.delivered_at else None,
        "timeline": [{"status": h.new_status, "note": h.note,
                      "created_at": h.created_at.isoformat() if h.created_at else ""} for h in history],
        "latest_attempt": (serialize_attempt(attempts[-1]) if attempts else None),
        "ship_city": sh.ship_city, "ship_state": sh.ship_state, "ship_pincode": sh.ship_pincode,
    }


def _summarize(db: Session, shipments: List[Shipment]) -> List[dict]:
    if not shipments:
        return []
    ids = [s.id for s in shipments]
    counts = {sid: int(c) for sid, c in (
        db.query(ShipmentItem.shipment_id, sqlfunc.count(ShipmentItem.id))
        .filter(ShipmentItem.shipment_id.in_(ids)).group_by(ShipmentItem.shipment_id).all()
    )}
    user_ids = {s.user_id for s in shipments if s.user_id}
    users = {u.id: u for u in db.query(Users).filter(Users.id.in_(user_ids)).all()} if user_ids else {}
    out = []
    for s in shipments:
        u = users.get(s.user_id)
        out.append({
            "id": str(s.id), "order_id": str(s.order_id), "order_number": str(s.order_id)[:8].upper(),
            "status": s.status, "is_prepaid": bool(s.is_prepaid),
            "cod_amount": (_money(s.cod_amount) if s.cod_amount is not None else None),
            "cod_collected": bool(s.cod_collected), "cod_remitted": bool(s.cod_remitted),
            "courier_name": s.courier_name, "awb_number": s.awb_number,
            "ship_city": s.ship_city, "ship_state": s.ship_state, "ship_pincode": s.ship_pincode,
            "customer_name": (u.name or "") if u else "", "customer_phone": (u.phone or "") if u else "",
            "item_count": counts.get(s.id, 0),
            "created_at": s.created_at.isoformat() if s.created_at else "",
        })
    return out


# ═══════════════════════════════════════════════════════════════════
# Operations
# ═══════════════════════════════════════════════════════════════════

def create_shipment(db: Session, admin_id: Any, payload) -> dict:
    order = (
        db.query(Order).options(selectinload(Order.order_items)).filter(Order.id == _as_uuid(payload.order_id, "order_id")).first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if (order.status or "").lower() == "cancelled":
        raise HTTPException(status_code=400, detail="Cannot create a shipment for a cancelled order")
    
    # Default guard: one active shipment per order. A second shipment is only
    # allowed when the caller explicitly passes items (partial-shipment path).
    if not payload.items:
        existing = (
            db.query(Shipment)
            .filter(Shipment.order_id == order.id, Shipment.status != "cancelled")
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Order already has an active shipment ({str(existing.id)[:8]}). Open it instead.",
            )

    # items already in a non-cancelled shipment are unavailable (partial-shipment safe)
    shipped = {
        str(oi_id) for (oi_id,) in (
            db.query(ShipmentItem.order_item_id)
            .join(Shipment, ShipmentItem.shipment_id == Shipment.id)
            .filter(Shipment.order_id == order.id, Shipment.status != "cancelled").all()
        )
    }
    order_items = {str(oi.id): oi for oi in (order.order_items or [])}

    if payload.items:
        chosen = []
        for it in payload.items:
            oi = order_items.get(str(it.order_item_id))
            if not oi:
                raise HTTPException(status_code=400, detail=f"Item {it.order_item_id} not in this order")
            if str(oi.id) in shipped:
                raise HTTPException(status_code=400, detail=f"Item {it.order_item_id} is already in a shipment")
            chosen.append((oi, int(it.quantity)))
    else:
        chosen = [(oi, int(oi.quantity)) for oi in (order.order_items or []) if str(oi.id) not in shipped]
        if not chosen:
            raise HTTPException(status_code=400, detail="All items in this order are already in a shipment")

    is_prepaid = order.razorpay_payment_id is not None
    parsed = _parse_shipping_address(order.shipping_address)

    sh = Shipment(
        order_id=order.id, user_id=order.user_id, status="ready_to_pack",
        is_prepaid=is_prepaid,
        cod_amount=(None if is_prepaid else Decimal(str(order.total_amount or 0))),
        ship_name=payload.ship_name or parsed["name"],
        ship_phone=payload.ship_phone or "",
        ship_line1=payload.ship_line1 or parsed["line1"],
        ship_city=payload.ship_city or parsed["city"],
        ship_state=payload.ship_state or parsed["state"],
        ship_pincode=payload.ship_pincode or parsed["pincode"],
    )
    db.add(sh)
    db.flush()
    for oi, qty in chosen:
        db.add(ShipmentItem(shipment_id=sh.id, order_item_id=oi.id, product_id=oi.product_id, quantity=qty))
    _record_history(db, sh, None, "ready_to_pack", _as_uuid(admin_id), "Shipment created")
    db.commit(); db.refresh(sh)
    return serialize_shipment_full(db, _load(db, sh.id))


def _assert_dispatchable(db: Session, sh: Shipment):
    """Consistency guard: a shipment flagged prepaid must have a captured payment
    on its order. (is_prepaid is derived from payment presence at creation, so
    this catches data drift. True 'unpaid prepaid' detection would need a
    payment_status the schema doesn't have — documented limitation.)"""
    if sh.is_prepaid:
        order = db.query(Order).filter(Order.id == sh.order_id).first()
        if order and not order.razorpay_payment_id:
            raise HTTPException(status_code=400, detail="Prepaid order has no captured payment — cannot dispatch")


def pack_shipment(db: Session, admin_id: Any, shipment_id: Any, payload) -> dict:
    sh = _load(db, shipment_id)
    _assert_dispatchable(db, sh)
    if payload.package_weight is not None: sh.package_weight = Decimal(str(payload.package_weight))
    if payload.package_length is not None: sh.package_length = Decimal(str(payload.package_length))
    if payload.package_width  is not None: sh.package_width  = Decimal(str(payload.package_width))
    if payload.package_height is not None: sh.package_height = Decimal(str(payload.package_height))
    _transition(db, sh, "packed", _as_uuid(admin_id), payload.note or "Packed")
    db.commit(); db.refresh(sh)
    return serialize_shipment_full(db, _load(db, sh.id))


def assign_courier(db: Session, admin_id: Any, shipment_id: Any, payload) -> dict:
    sh = _load(db, shipment_id)
    if sh.status == "delivered" and not payload.override:
        raise HTTPException(status_code=400, detail="Cannot edit courier/AWB after delivery without override")
    cp_id = _as_uuid(payload.courier_partner_id, "courier_partner_id") if payload.courier_partner_id else None
    sh.courier_partner_id = cp_id
    sh.courier_name = payload.courier_name
    sh.courier_service = payload.courier_service
    sh.awb_number = payload.awb_number
    sh.tracking_url = _build_tracking_url(db, cp_id, payload.awb_number, payload.tracking_url)
    if payload.shipping_cost is not None: sh.shipping_cost = Decimal(str(payload.shipping_cost))
    if payload.expected_delivery_date is not None: sh.expected_delivery_date = payload.expected_delivery_date
    if sh.status == "packed":
        _transition(db, sh, "label_generated", _as_uuid(admin_id), payload.note or f"Courier {payload.courier_name} / AWB {payload.awb_number}")
    else:
        _record_history(db, sh, sh.status, sh.status, _as_uuid(admin_id), payload.note or "Courier details updated")
    db.commit(); db.refresh(sh)
    return serialize_shipment_full(db, _load(db, sh.id))


def attach_label(db: Session, admin_id: Any, shipment_id: Any, payload) -> dict:
    sh = _load(db, shipment_id)
    sh.label_url = payload.label_url
    sh.label_public_id = payload.label_public_id
    sh.label_generated_at = _utcnow()
    db.add(ShippingLabel(shipment_id=sh.id, label_url=payload.label_url, label_public_id=payload.label_public_id,
                         file_name=payload.file_name, generated_by=payload.generated_by or "manual"))
    if sh.status == "packed":
        _transition(db, sh, "label_generated", _as_uuid(admin_id), payload.note or "Label attached")
    else:
        _record_history(db, sh, sh.status, sh.status, _as_uuid(admin_id), payload.note or "Label attached")
    db.commit(); db.refresh(sh)
    return serialize_shipment_full(db, _load(db, sh.id))


def schedule_pickup(db: Session, admin_id: Any, shipment_id: Any, payload) -> dict:
    sh = _load(db, shipment_id)
    sh.pickup_scheduled_at = payload.pickup_scheduled_at or _utcnow()
    if sh.status in ("packed", "label_generated"):
        _transition(db, sh, "pickup_scheduled", _as_uuid(admin_id), payload.note or "Pickup scheduled")
    elif sh.status == "pickup_scheduled":
        _record_history(db, sh, sh.status, sh.status, _as_uuid(admin_id), payload.note or "Pickup rescheduled")
    else:
        raise HTTPException(status_code=409, detail=f"Cannot schedule pickup from '{sh.status}'")
    db.commit(); db.refresh(sh)
    return serialize_shipment_full(db, _load(db, sh.id))


def mark_pickup_failed(db: Session, admin_id: Any, shipment_id: Any, payload) -> dict:
    sh = _load(db, shipment_id)
    if sh.status != "pickup_scheduled":
        raise HTTPException(status_code=409, detail="Pickup can only fail while scheduled")
    sh.pickup_attempts = int(sh.pickup_attempts or 0) + 1
    note = f"Pickup failed (attempt {sh.pickup_attempts})" + (f": {payload.reason}" if payload.reason else "")
    _record_history(db, sh, sh.status, sh.status, _as_uuid(admin_id), payload.note or note)
    db.commit(); db.refresh(sh)
    return serialize_shipment_full(db, _load(db, sh.id))


def mark_picked_up(db: Session, admin_id: Any, shipment_id: Any, note: Optional[str] = None) -> dict:
    sh = _load(db, shipment_id)
    _transition(db, sh, "picked_up", _as_uuid(admin_id), note or "Picked up by courier")
    db.commit(); db.refresh(sh)
    return serialize_shipment_full(db, _load(db, sh.id))


def set_status(db: Session, admin_id: Any, shipment_id: Any, payload) -> dict:
    sh = _load(db, shipment_id)
    _transition(db, sh, payload.status, _as_uuid(admin_id), payload.note)
    db.commit(); db.refresh(sh)
    return serialize_shipment_full(db, _load(db, sh.id))


def record_delivery_attempt(db: Session, admin_id: Any, shipment_id: Any, payload) -> dict:
    sh = _load(db, shipment_id)
    n = db.query(sqlfunc.count(DeliveryAttempt.id)).filter(DeliveryAttempt.shipment_id == sh.id).scalar() or 0
    db.add(DeliveryAttempt(
        shipment_id=sh.id, attempt_number=int(n) + 1, attempted_at=_utcnow(),
        status=payload.status, failure_reason=payload.failure_reason,
        courier_remarks=payload.courier_remarks, next_attempt_at=payload.next_attempt_at,
        customer_contacted=payload.customer_contacted,
    ))
    if payload.status == "delivered":
        _transition(db, sh, "delivered", _as_uuid(admin_id), payload.note or "Delivered")
    elif payload.status == "failed":
        if sh.status not in ("delivery_failed",):
            _transition(db, sh, "delivery_failed", _as_uuid(admin_id), payload.note or f"Delivery failed: {payload.failure_reason or 'unknown'}")
    db.commit(); db.refresh(sh)
    return serialize_shipment_full(db, _load(db, sh.id))


def initiate_rto(db: Session, admin_id: Any, shipment_id: Any, payload) -> dict:
    sh = _load(db, shipment_id)
    _transition(db, sh, "rto_initiated", _as_uuid(admin_id), payload.note or "RTO initiated")
    db.commit(); db.refresh(sh)
    return serialize_shipment_full(db, _load(db, sh.id))


def receive_rto(db: Session, admin_id: Any, shipment_id: Any, payload) -> dict:
    """RTO received → returned_to_origin. RESTOCK happens here (resellable units
    add back to Product.count; damaged do not) — same rule as returns.receive."""
    sh = _load(db, shipment_id)
    items_by_id = {str(it.id): it for it in sh.items}
    for cond in payload.items:
        it = items_by_id.get(str(cond.shipment_item_id))
        if not it:
            raise HTTPException(status_code=400, detail=f"Shipment item {cond.shipment_item_id} not found")
        it.condition_status = cond.condition_status
        if cond.condition_status == "resellable":
            restock = cond.restock_quantity if cond.restock_quantity is not None else it.quantity
            restock = max(0, min(int(restock), int(it.quantity)))
            it.is_resellable = True
            it.restock_quantity = restock
            if restock > 0:
                p = db.query(Product).filter(Product.id == it.product_id).first()
                if p:
                    p.count = int(p.count or 0) + restock
        else:
            it.is_resellable = False
            it.restock_quantity = 0
    _transition(db, sh, "returned_to_origin", _as_uuid(admin_id), payload.note or "RTO received at warehouse")
    db.commit(); db.refresh(sh)
    return serialize_shipment_full(db, _load(db, sh.id))


def update_cod(db: Session, admin_id: Any, shipment_id: Any, payload) -> dict:
    sh = _load(db, shipment_id)
    if sh.is_prepaid:
        raise HTTPException(status_code=400, detail="This is a prepaid shipment — no COD to settle")
    if payload.action == "collect":
        sh.cod_collected = True
        if sh.cod_collected_at is None:
            sh.cod_collected_at = _utcnow()
        _record_history(db, sh, sh.status, sh.status, _as_uuid(admin_id), payload.note or "COD collected")
    else:  # remit
        if not sh.cod_collected:
            raise HTTPException(status_code=400, detail="Mark COD collected before remittance")
        sh.cod_remitted = True
        sh.cod_remittance_reference = payload.reference
        sh.cod_remitted_at = _utcnow()
        _record_history(db, sh, sh.status, sh.status, _as_uuid(admin_id), payload.note or f"COD remitted ({payload.reference or 'no ref'})")
    db.commit(); db.refresh(sh)
    return serialize_shipment_full(db, _load(db, sh.id))


def cancel_shipment(db: Session, admin_id: Any, shipment_id: Any, payload) -> dict:
    """Cancel before dispatch. Does NOT touch stock (rule #19)."""
    sh = _load(db, shipment_id)
    _transition(db, sh, "cancelled", _as_uuid(admin_id), payload.note or "Shipment cancelled")
    db.commit(); db.refresh(sh)
    return serialize_shipment_full(db, _load(db, sh.id))


def set_admin_notes(db: Session, shipment_id: Any, admin_notes: str) -> dict:
    sh = _load(db, shipment_id)
    sh.admin_notes = admin_notes
    db.commit(); db.refresh(sh)
    return serialize_shipment_full(db, _load(db, sh.id))


# ── Read / list ──

def get_shipment_admin(db: Session, shipment_id: Any) -> dict:
    return serialize_shipment_full(db, _load(db, shipment_id))


def list_shipments_admin(db: Session, skip: int = 0, limit: int = 20,
                         status: Optional[str] = None, courier: Optional[str] = None,
                         payment: Optional[str] = None, search: Optional[str] = None,
                         city: Optional[str] = None, state: Optional[str] = None, pincode: Optional[str] = None,
                         date_from: Optional[str] = None, date_to: Optional[str] = None,
                         sort: str = "latest") -> dict:
    q = db.query(Shipment)
    if status:  q = q.filter(Shipment.status == status)
    if courier: q = q.filter(Shipment.courier_name.ilike(f"%{courier}%"))
    if payment == "cod":     q = q.filter(Shipment.is_prepaid == False)  # noqa: E712
    if payment == "prepaid": q = q.filter(Shipment.is_prepaid == True)   # noqa: E712
    if city:    q = q.filter(Shipment.ship_city.ilike(f"%{city}%"))
    if state:   q = q.filter(Shipment.ship_state.ilike(f"%{state}%"))
    if pincode: q = q.filter(Shipment.ship_pincode == pincode)
    if date_from:
        try: q = q.filter(Shipment.created_at >= datetime.datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc))
        except ValueError: pass
    if date_to:
        try: q = q.filter(Shipment.created_at < (datetime.datetime.fromisoformat(date_to) + datetime.timedelta(days=1)).replace(tzinfo=timezone.utc))
        except ValueError: pass
    if search:
        term = f"%{search}%"
        q = (q.outerjoin(Users, Shipment.user_id == Users.id)
              .filter(or_(
                  cast(Shipment.order_id, String).ilike(term),
                  Shipment.awb_number.ilike(term),
                  Users.name.ilike(term),
                  Users.phone.ilike(term),
              )))
    total = q.count()
    if sort == "oldest":
        q = q.order_by(Shipment.created_at.asc())
    elif sort == "status":
        q = q.order_by(Shipment.status.asc(), Shipment.created_at.desc())
    elif sort == "deadline":
        q = q.order_by(Shipment.expected_delivery_date.asc().nullslast())
    else:
        q = q.order_by(Shipment.created_at.desc())
    rows = q.offset(skip).limit(limit).all()
    return {"data": _summarize(db, rows), "totalCount": total, "page": (skip // limit) + 1 if limit else 1, "limit": limit}


def shipments_summary(db: Session) -> dict:
    """Dashboard counts by status + COD-pending + a few rollups."""
    rows = db.query(Shipment.status, sqlfunc.count(Shipment.id)).group_by(Shipment.status).all()
    by_status = {s: int(c) for s, c in rows}
    def g(*keys): return sum(by_status.get(k, 0) for k in keys)
    cod_pending = (db.query(sqlfunc.count(Shipment.id))
                   .filter(Shipment.is_prepaid == False, Shipment.cod_collected == True, Shipment.cod_remitted == False)  # noqa: E712
                   .scalar() or 0)
    return {
        "by_status": by_status,
        "pending_packing":   g("pending", "ready_to_pack"),
        "ready_for_pickup":  g("packed", "label_generated", "pickup_scheduled"),
        "in_transit":        g("picked_up", "in_transit"),
        "out_for_delivery":  g("out_for_delivery"),
        "delivered":         g("delivered"),
        "failed":            g("delivery_failed"),
        "rto":               g("rto_initiated", "returned_to_origin"),
        "exceptions":        g("lost", "damaged_in_transit"),
        "cod_remittance_pending": int(cod_pending),
        "total": sum(by_status.values()),
    }


def get_order_tracking(db: Session, order_id: Any) -> dict:
    """Customer-safe: the most recent shipment for an order (or empty)."""
    sh = (db.query(Shipment)
          .options(selectinload(Shipment.status_history), selectinload(Shipment.attempts))
          .filter(Shipment.order_id == _as_uuid(order_id, "order_id"))
          .order_by(Shipment.created_at.desc()).first())
    if not sh:
        return {"has_shipment": False}
    out = serialize_tracking(db, sh)
    out["has_shipment"] = True
    return out