# app/returns/services.py
"""
Return subsystem — business logic (Phase 13, Stage 3a).

Owns: eligibility, the status state machine (+ history), and ALL money/stock
side-effects (restock on resellable-receive, stock-check + deduct on
replacement-dispatch, idempotent refund records).

Each public function performs one logical operation and commits its own
transaction, returning a serialized dict. Routers (Stage 3b) stay thin.

Conventions deliberately matched to the existing codebase:
- Hand-built serializer dicts (like _serialize_order) — never Pydantic ORM-mode.
- Decimal for money (matches orders.total_amount / order_items.price); converted
  to float only at the serialization boundary.
- tz-aware UTC datetimes throughout (orders.delivered_at is TIMESTAMPTZ), to
  avoid the naive/aware comparison TypeError hit in Phase 11.
"""
from __future__ import annotations

import datetime
from datetime import timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import String, cast, or_
from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session, joinedload, selectinload

from app.returns.models import (
    ReturnRequest, ReturnItem, ReturnProof, ReturnStatusHistory,
    Refund, ReplacementShipment,
)
from app.orders.models import Order, OrderItem
from app.products.models import Product
from app.users.models import Users
from app.store_settings.models import StoreSetting

from app.returns.razorpay_refund import create_gateway_refund


def _map_gateway_status(gw: Optional[str]) -> str:
    """Razorpay-native status → our workflow refund status."""
    if gw == "processed":
        return "completed"
    if gw == "failed":
        return "failed"
    return "initiated"   # 'pending' or unknown → in-flight (normal-speed refunds sit here for days)

# ═══════════════════════════════════════════════════════════════════
# State machine — the ONLY legal status transitions
# ═══════════════════════════════════════════════════════════════════

TRANSITIONS: Dict[str, set] = {
    "requested":              {"under_review", "approved", "rejected", "cancelled_by_customer"},
    "under_review":           {"approved", "rejected", "cancelled_by_customer"},
    "approved":               {"pickup_scheduled", "picked_up", "received",
                               "refunded", "replacement_dispatched", "rejected"},
    "rejected":               set(),                       # terminal
    "pickup_scheduled":       {"picked_up", "received", "cancelled_by_customer"},
    "picked_up":              {"received"},
    "received":               {"refunded", "replacement_dispatched", "completed"},
    "replacement_dispatched": {"completed"},
    "refunded":               {"completed"},
    "completed":              set(),                       # terminal
    "cancelled_by_customer":  set(),                       # terminal
}


# ═══════════════════════════════════════════════════════════════════
# Small helpers
# ═══════════════════════════════════════════════════════════════════

def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(timezone.utc)


def _ensure_aware(dt: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _as_uuid(val: Any, what: str = "id") -> UUID:
    try:
        return UUID(str(val))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid {what}")


def _money(v: Any) -> float:
    return float(v) if v is not None else 0.0


def get_return_window_days(session: Session) -> int:
    """Admin-configurable return window (StoreSetting 'return_window_days', default 7)."""
    row = session.query(StoreSetting).filter(StoreSetting.key == "return_window_days").first()
    if row and row.value:
        try:
            return int(row.value)
        except (ValueError, TypeError):
            pass
    return 7


def _record_history(session: Session, rr: ReturnRequest, old: Optional[str],
                    new: str, actor_id: Optional[UUID], note: Optional[str]) -> None:
    session.add(ReturnStatusHistory(
        return_request_id=rr.id,
        old_status=old,
        new_status=new,
        changed_by_admin_id=actor_id,
        note=note,
    ))


def _transition(session: Session, rr: ReturnRequest, new_status: str,
                actor_id: Optional[UUID] = None, note: Optional[str] = None) -> None:
    """Validate a status change against TRANSITIONS, apply it, write history.
    No-op (and no history row) if already in the target status — keeps the
    money/stock callers idempotent against double-clicks."""
    old = rr.status
    if old == new_status:
        return
    if new_status not in TRANSITIONS.get(old, set()):
        raise HTTPException(status_code=409, detail=f"Cannot move a return from '{old}' to '{new_status}'")
    rr.status = new_status
    _record_history(session, rr, old, new_status, actor_id, note)


def _already_returned_qty(session: Session, order_item_id: UUID) -> int:
    """Units of an order_item already tied up in non-terminal-rejected returns.
    Rejected / customer-cancelled returns FREE the quantity again."""
    val = (
        session.query(sqlfunc.coalesce(sqlfunc.sum(ReturnItem.quantity), 0))
        .join(ReturnRequest, ReturnItem.return_request_id == ReturnRequest.id)
        .filter(ReturnItem.order_item_id == order_item_id)
        .filter(~ReturnRequest.status.in_(["rejected", "cancelled_by_customer"]))
        .scalar()
    )
    return int(val or 0)


def _load_return(session: Session, return_id: Any) -> ReturnRequest:
    rr = (
        session.query(ReturnRequest)
        .options(
            selectinload(ReturnRequest.items),
            selectinload(ReturnRequest.proofs),
            selectinload(ReturnRequest.status_history),
            joinedload(ReturnRequest.refund),
            joinedload(ReturnRequest.replacement),
        )
        .filter(ReturnRequest.id == _as_uuid(return_id, "return_id"))
        .first()
    )
    if not rr:
        raise HTTPException(status_code=404, detail="Return request not found")
    return rr


def _load_owned(session: Session, return_id: Any, user_id: Any) -> ReturnRequest:
    rr = _load_return(session, return_id)
    if str(rr.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="This return does not belong to you")
    return rr


# ═══════════════════════════════════════════════════════════════════
# Serializers (hand-built — own the response shape)
# ═══════════════════════════════════════════════════════════════════

def serialize_return_item(ri: ReturnItem) -> dict:
    return {
        "id":               str(ri.id),
        "order_item_id":    str(ri.order_item_id),
        "product_id":       str(ri.product_id),
        "quantity":         int(ri.quantity),
        "item_price":       _money(ri.item_price),
        "condition_status": ri.condition_status,
        "restock_quantity": int(ri.restock_quantity or 0),
        "is_resellable":    bool(ri.is_resellable),
    }


def serialize_proof(p: ReturnProof) -> dict:
    return {
        "id":         str(p.id),
        "file_url":   p.file_url,
        "file_type":  p.file_type,
        "created_at": p.created_at.isoformat() if p.created_at else "",
    }


def serialize_history(h: ReturnStatusHistory) -> dict:
    return {
        "id":         str(h.id),
        "old_status": h.old_status,
        "new_status": h.new_status,
        "note":       h.note,
        "created_at": h.created_at.isoformat() if h.created_at else "",
    }


def serialize_refund(r: Optional[Refund]) -> Optional[dict]:
    if not r:
        return None
    return {
        "id":                    str(r.id),
        "amount":                _money(r.amount),
        "method":                r.method,
        "status":                r.status,
        "transaction_reference": r.transaction_reference,
        "processed_at":          r.processed_at.isoformat() if r.processed_at else None,
                # gateway tracking
        "gateway_refund_id":     getattr(r, "gateway_refund_id", None),
        "gateway_status":        getattr(r, "gateway_status", None),
        "speed":                 getattr(r, "speed", None),
    }


def serialize_replacement(s: Optional[ReplacementShipment]) -> Optional[dict]:
    if not s:
        return None
    return {
        "id":              str(s.id),
        "product_id":      str(s.product_id),
        "quantity":        int(s.quantity),
        "status":          s.status,
        "tracking_number": s.tracking_number,
        "dispatched_at":   s.dispatched_at.isoformat() if s.dispatched_at else None,
        "delivered_at":    s.delivered_at.isoformat() if s.delivered_at else None,
    }


def serialize_return_full(session: Session, rr: ReturnRequest, include_order: bool = False) -> dict:
    """Full detail dict: items (enriched with product name/image/current stock),
    proofs, timeline, refund, replacement, and optionally order + customer."""
    product_ids = {ri.product_id for ri in rr.items if ri.product_id}
    products: Dict[Any, dict] = {}
    if product_ids:
        for p in session.query(Product).filter(Product.id.in_(product_ids)).all():
            img = None
            pi = getattr(p, "product_image", None)
            if isinstance(pi, list) and pi:
                first = pi[0]
                img = first.get("url") if isinstance(first, dict) else None
            products[p.id] = {"name": p.name, "image": img, "current_stock": int(getattr(p, "count", 0) or 0)}

    items_out = []
    for ri in rr.items:
        d = serialize_return_item(ri)
        pinfo = products.get(ri.product_id, {})
        d["product_name"]  = pinfo.get("name", "")
        d["product_image"] = pinfo.get("image")
        d["current_stock"] = pinfo.get("current_stock", 0)
        items_out.append(d)

    _epoch = datetime.datetime.min.replace(tzinfo=timezone.utc)
    history_sorted = sorted(rr.status_history, key=lambda x: _ensure_aware(x.created_at) or _epoch)

    out: dict = {
        "id":                  str(rr.id),
        "order_id":            str(rr.order_id),
        "user_id":             str(rr.user_id),
        "status":              rr.status,
        "request_type":        rr.request_type,
        "reason":              rr.reason,
        "description":         rr.description,
        "total_refund_amount": (_money(rr.total_refund_amount) if rr.total_refund_amount is not None else None),
        "admin_notes":         rr.admin_notes,
        "rejection_reason":    rr.rejection_reason,
        "created_at":          rr.created_at.isoformat() if rr.created_at else "",
        "updated_at":          rr.updated_at.isoformat() if rr.updated_at else "",
        "items":               items_out,
        "proofs":              [serialize_proof(p) for p in rr.proofs],
        "status_history":      [serialize_history(h) for h in history_sorted],
        "refund":              serialize_refund(rr.refund),
        "replacement":         serialize_replacement(rr.replacement),
    }

    if include_order:
        order = session.query(Order).options(joinedload(Order.user)).filter(Order.id == rr.order_id).first()
        if order:
            u = order.user
            out["order"] = {
                "id":               str(order.id),
                "order_number":     str(order.id)[:8].upper(),
                "status":           order.status,
                "total_amount":     _money(order.total_amount),
                "created_at":       order.created_at.isoformat() if order.created_at else "",
                "delivered_at":     order.delivered_at.isoformat() if getattr(order, "delivered_at", None) else None,
                "shipping_address": order.shipping_address or "",
                "razorpay_payment_id": getattr(order, "razorpay_payment_id", None),  # ADD — drives gateway-refund availability
            }
            out["customer"] = {
                "id":    str(u.id) if u else "",
                "name":  (u.name or "") if u else "",
                "email": (u.email or "") if u else "",
                "phone": (u.phone or "") if u else "",
            }
    return out


def _summarize(session: Session, returns: List[ReturnRequest]) -> List[dict]:
    """Lightweight list rows (customer name + item counts), batch-loaded (no N+1)."""
    if not returns:
        return []
    rr_ids   = [r.id for r in returns]
    user_ids = {r.user_id for r in returns}

    users = {u.id: u for u in session.query(Users).filter(Users.id.in_(user_ids)).all()} if user_ids else {}

    item_rows = (
        session.query(
            ReturnItem.return_request_id,
            sqlfunc.count(ReturnItem.id),
            sqlfunc.coalesce(sqlfunc.sum(ReturnItem.quantity), 0),
        )
        .filter(ReturnItem.return_request_id.in_(rr_ids))
        .group_by(ReturnItem.return_request_id)
        .all()
    )
    counts = {rid: (int(c), int(q or 0)) for rid, c, q in item_rows}

    out = []
    for r in returns:
        u = users.get(r.user_id)
        lines, units = counts.get(r.id, (0, 0))
        out.append({
            "id":                  str(r.id),
            "order_id":            str(r.order_id),
            "order_number":        str(r.order_id)[:8].upper(),
            "status":              r.status,
            "request_type":        r.request_type,
            "reason":              r.reason,
            "customer_name":       (u.name or "") if u else "",
            "customer_email":      (u.email or "") if u else "",
            "item_count":          lines,
            "total_units":         units,
            "total_refund_amount": (_money(r.total_refund_amount) if r.total_refund_amount is not None else None),
            "created_at":          r.created_at.isoformat() if r.created_at else "",
        })
    return out


# ═══════════════════════════════════════════════════════════════════
# Customer operations
# ═══════════════════════════════════════════════════════════════════

def create_return_request(session: Session, user_id: Any, payload) -> dict:
    order_uuid = _as_uuid(payload.order_id, "order_id")
    order = (
        session.query(Order)
        .options(selectinload(Order.order_items).joinedload(OrderItem.product))
        .filter(Order.id == order_uuid)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if str(order.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="This order does not belong to you")

    # ── Eligibility ──────────────────────────────────────────────
    if (order.status or "").lower() != "delivered":
        raise HTTPException(status_code=400, detail="Returns can only be requested for delivered orders")
    delivered_at = _ensure_aware(getattr(order, "delivered_at", None))
    if delivered_at is None:
        raise HTTPException(
            status_code=400,
            detail="Delivery date is unavailable for this order, so a return window cannot be determined",
        )
    window = get_return_window_days(session)
    if _utcnow() - delivered_at > datetime.timedelta(days=window):
        raise HTTPException(status_code=400, detail=f"The {window}-day return window for this order has expired")

    order_items_by_id = {str(oi.id): oi for oi in (order.order_items or [])}

    validated: List[tuple] = []
    for it in payload.items:
        oi = order_items_by_id.get(str(it.order_item_id))
        if not oi:
            raise HTTPException(status_code=400, detail=f"Item {it.order_item_id} is not part of this order")
        purchased = int(oi.quantity or 0)
        already   = _already_returned_qty(session, oi.id)
        if it.quantity + already > purchased:
            remaining = max(purchased - already, 0)
            pname = getattr(getattr(oi, "product", None), "name", None) or "item"
            raise HTTPException(
                status_code=400,
                detail=(f"Cannot return {it.quantity} of '{pname}': "
                        f"{purchased} purchased, {already} already in a return, {remaining} remaining"),
            )
        validated.append((oi, it.quantity))

    rr = ReturnRequest(
        order_id=order.id,
        user_id=order.user_id,
        status="requested",
        request_type=payload.request_type,
        reason=payload.reason,
        description=payload.description,
    )
    session.add(rr)
    session.flush()  # populate rr.id

    for oi, qty in validated:
        session.add(ReturnItem(
            return_request_id=rr.id,
            order_item_id=oi.id,
            product_id=oi.product_id,
            quantity=qty,
            item_price=oi.price,            # snapshot of price PAID, not current price
            condition_status="pending",
            restock_quantity=0,
            is_resellable=False,
        ))

    _record_history(session, rr, None, "requested", _as_uuid(user_id), "Return requested by customer")
    session.commit()
    session.refresh(rr)
    return serialize_return_full(session, rr, include_order=True)


def list_my_returns(session: Session, user_id: Any) -> List[dict]:
    returns = (
        session.query(ReturnRequest)
        .filter(ReturnRequest.user_id == _as_uuid(user_id))
        .order_by(ReturnRequest.created_at.desc())
        .all()
    )
    return _summarize(session, returns)


def get_my_return(session: Session, user_id: Any, return_id: Any) -> dict:
    rr = _load_owned(session, return_id, user_id)
    return serialize_return_full(session, rr, include_order=True)


def cancel_return(session: Session, user_id: Any, return_id: Any, note: Optional[str] = None) -> dict:
    rr = _load_owned(session, return_id, user_id)
    if rr.status not in ("requested", "under_review"):
        raise HTTPException(status_code=400, detail="This return can no longer be cancelled")
    _transition(session, rr, "cancelled_by_customer", actor_id=_as_uuid(user_id), note=note or "Cancelled by customer")
    session.commit()
    session.refresh(rr)
    return serialize_return_full(session, rr, include_order=True)


def add_proof_url(session: Session, return_id: Any, user_id: Any, file_url: str,
                  public_id: Optional[str] = None, file_type: str = "image",
                  is_admin: bool = False) -> dict:
    """Record a Cloudinary URL as proof. Router does the upload, then calls this."""
    rr = _load_return(session, return_id) if is_admin else _load_owned(session, return_id, user_id)
    if not is_admin and rr.status not in ("requested", "under_review"):
        raise HTTPException(status_code=400, detail="Proof can only be added while the return is under review")
    session.add(ReturnProof(return_request_id=rr.id, file_url=file_url, public_id=public_id, file_type=file_type))
    session.commit()
    session.refresh(rr)
    return serialize_return_full(session, rr, include_order=True)


# ═══════════════════════════════════════════════════════════════════
# Admin operations
# ═══════════════════════════════════════════════════════════════════

def list_returns_admin(session: Session, skip: int = 0, limit: int = 20,
                       status: Optional[str] = None, reason: Optional[str] = None,
                       search: Optional[str] = None,
                       date_from: Optional[str] = None, date_to: Optional[str] = None) -> dict:
    q = session.query(ReturnRequest)

    if status:
        q = q.filter(ReturnRequest.status == status)
    if reason:
        q = q.filter(ReturnRequest.reason == reason)

    if date_from:
        try:
            q = q.filter(ReturnRequest.created_at >= datetime.datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(ReturnRequest.created_at < (datetime.datetime.fromisoformat(date_to) + datetime.timedelta(days=1)).replace(tzinfo=timezone.utc))
        except ValueError:
            pass

    if search:
        term = f"%{search}%"
        # product-name match via subquery (avoids row multiplication on join)
        prod_subq = (
            session.query(ReturnItem.return_request_id)
            .join(Product, ReturnItem.product_id == Product.id)
            .filter(Product.name.ilike(term))
        )
        q = (
            q.outerjoin(Users, ReturnRequest.user_id == Users.id)
            .filter(or_(
                cast(ReturnRequest.order_id, String).ilike(term),
                cast(ReturnRequest.id, String).ilike(term),
                Users.name.ilike(term),
                Users.email.ilike(term),
                ReturnRequest.id.in_(prod_subq),
            ))
        )

    total   = q.count()
    returns = q.order_by(ReturnRequest.created_at.desc()).offset(skip).limit(limit).all()

    return {
        "data":       _summarize(session, returns),
        "totalCount": total,
        "page":       (skip // limit) + 1 if limit else 1,
        "limit":      limit,
    }


def get_return_admin(session: Session, return_id: Any) -> dict:
    rr = _load_return(session, return_id)
    return serialize_return_full(session, rr, include_order=True)


def admin_set_status(session: Session, admin_id: Any, return_id: Any,
                     new_status: str, note: Optional[str] = None) -> dict:
    rr = _load_return(session, return_id)
    _transition(session, rr, new_status, actor_id=_as_uuid(admin_id), note=note)
    session.commit()
    session.refresh(rr)
    return serialize_return_full(session, rr, include_order=True)


def set_admin_notes(session: Session, return_id: Any, admin_notes: str) -> dict:
    rr = _load_return(session, return_id)
    rr.admin_notes = admin_notes
    session.commit()
    session.refresh(rr)
    return serialize_return_full(session, rr, include_order=True)


def approve_return(session: Session, admin_id: Any, return_id: Any,
                   admin_notes: Optional[str] = None, note: Optional[str] = None) -> dict:
    rr = _load_return(session, return_id)
    # D3 (server-side gate): a damaged/defective claim cannot be approved without
    # evidence. rr.proofs is eager-loaded by _load_return, so this is cheap.
    if rr.reason in ("damaged", "defective") and not rr.proofs:
        raise HTTPException(
            status_code=400,
            detail="Cannot approve: photo/video proof is required for damaged or defective items",
        )
    if admin_notes is not None:
        rr.admin_notes = admin_notes
    _transition(session, rr, "approved", actor_id=_as_uuid(admin_id), note=note or "Approved")
    session.commit()
    session.refresh(rr)
    return serialize_return_full(session, rr, include_order=True)


def reject_return(session: Session, admin_id: Any, return_id: Any,
                  rejection_reason: str, admin_notes: Optional[str] = None,
                  note: Optional[str] = None) -> dict:
    rr = _load_return(session, return_id)
    rr.rejection_reason = rejection_reason
    if admin_notes is not None:
        rr.admin_notes = admin_notes
    _transition(session, rr, "rejected", actor_id=_as_uuid(admin_id), note=note or f"Rejected: {rejection_reason}")
    session.commit()
    session.refresh(rr)
    return serialize_return_full(session, rr, include_order=True)


def receive_return(session: Session, admin_id: Any, return_id: Any,
                   item_conditions: list, note: Optional[str] = None) -> dict:
    """Mark received + set per-item condition. RESTOCK happens HERE and only here:
    resellable units are added back to Product.count; damaged units are not."""
    rr = _load_return(session, return_id)
    items_by_id = {str(ri.id): ri for ri in rr.items}

    for cond in item_conditions:
        ri = items_by_id.get(str(cond.return_item_id))
        if not ri:
            raise HTTPException(status_code=400, detail=f"Return item {cond.return_item_id} not found in this request")

        ri.condition_status = cond.condition_status
        if cond.condition_status == "resellable":
            restock = cond.restock_quantity if cond.restock_quantity is not None else ri.quantity
            restock = max(0, min(int(restock), int(ri.quantity)))   # never exceed returned qty
            ri.is_resellable    = True
            ri.restock_quantity = restock
            if restock > 0:
                product = session.query(Product).filter(Product.id == ri.product_id).first()
                if product:
                    product.count = int(product.count or 0) + restock
        else:  # damaged → no restock
            ri.is_resellable    = False
            ri.restock_quantity = 0

    _transition(session, rr, "received", actor_id=_as_uuid(admin_id), note=note or "Items received at warehouse")
    session.commit()
    session.refresh(rr)
    return serialize_return_full(session, rr, include_order=True)


def process_refund(session: Session, admin_id: Any, return_id: Any,
                   amount: Optional[float] = None, method: str = "manual",
                   status: str = "completed", transaction_reference: Optional[str] = None,
                   note: Optional[str] = None,
                   execute_gateway: bool = False, speed: str = "normal") -> dict:
    """
    Refund record for a return (1:1). Default amount = sum of PAID prices.

    MONEY MOVES ONLY when execute_gateway=True AND the order has a captured
    Razorpay payment id. That flag is set ONLY by the admin refund endpoint, on an
    explicit admin action. Every other caller — and the webhook — leaves it False
    and merely tracks. No stock change here (restock happens at receive).

    Idempotency: the gateway key is derived from the RETURN id (stable across
    retries, survives a rolled-back row) so a dropped response can't double-refund.
    Once a gateway_refund_id is stored, this never re-charges.
    """
    rr = _load_return(session, return_id)

    # ── Hard guard: a gateway refund already executed → never re-charge. ──
    if rr.refund is not None and getattr(rr.refund, "gateway_refund_id", None):
        return serialize_return_full(session, rr, include_order=True)

    default_amount = sum((Decimal(str(ri.item_price)) * ri.quantity) for ri in rr.items) if rr.items else Decimal("0")
    final_amount = Decimal(str(amount)) if amount is not None else default_amount

    # find-or-create the refund row (records the attempt + carries the result)
    refund = rr.refund
    if refund is None:
        refund = Refund(return_request_id=rr.id, amount=final_amount, method=method,
                        status=status, transaction_reference=transaction_reference)
        session.add(refund)
    else:
        refund.amount = final_amount
        refund.method = method
        refund.status = status
        refund.transaction_reference = transaction_reference

    gateway_failed = False

    if execute_gateway:
        # Canonical captured-payment field is Order.razorpay_payment_id (set by
        # payments.create-order-after-payment). NULL → not gateway-refundable.
        order = session.query(Order).filter(Order.id == rr.order_id).first()
        payment_id = getattr(order, "razorpay_payment_id", None) if order else None

        if not payment_id:
            # COD / no online payment → track manually, do NOT call the gateway.
            refund.method = "manual"
            refund.status = "pending"
            refund.gateway_status = None
        else:
            # Stable, retry-safe key (>=10 chars, allowed charset).
            idem_key = f"return-refund-{rr.id}"
            rfnd_id, gw_status = create_gateway_refund(
                payment_id=payment_id,
                amount=final_amount,
                idempotency_key=idem_key,
                speed=speed,
                notes={"return_id": str(rr.id), "order_id": str(rr.order_id)},
            )
            refund.method               = "razorpay"
            refund.speed                = speed
            refund.gateway_payment_id   = payment_id
            refund.gateway_refund_id    = rfnd_id
            refund.gateway_status       = gw_status
            refund.status               = _map_gateway_status(gw_status)
            refund.transaction_reference = rfnd_id   # human-visible reference
            if refund.status == "failed":
                gateway_failed = True

    if refund.status == "completed" and refund.processed_at is None:
        refund.processed_at = _utcnow()

    rr.total_refund_amount = final_amount

    # Move to 'refunded' once a refund is committed/initiated — but NOT on an
    # outright gateway failure (admin must follow up).
    if not gateway_failed and rr.status != "refunded":
        _transition(session, rr, "refunded", actor_id=_as_uuid(admin_id),
                    note=note or f"Refund {refund.status}: {final_amount}")

    session.commit()
    session.refresh(rr)
    return serialize_return_full(session, rr, include_order=True)


def dispatch_replacement(session: Session, admin_id: Any, return_id: Any,
                         product_id: Optional[str] = None, quantity: Optional[int] = None,
                         tracking_number: Optional[str] = None, status: str = "dispatched",
                         note: Optional[str] = None) -> dict:
    """Idempotent replacement shipment (1:1). Stock-CHECKED and DEDUCTED once, on
    the first dispatch/delivered transition. Defaults: first returned item's
    product, total returned units."""
    rr = _load_return(session, return_id)
    if not rr.items:
        raise HTTPException(status_code=400, detail="No items on this return to replace")

    target_product_id = _as_uuid(product_id, "product_id") if product_id else rr.items[0].product_id
    target_qty = int(quantity) if quantity is not None else sum(int(ri.quantity) for ri in rr.items)
    if target_qty < 1:
        raise HTTPException(status_code=400, detail="Replacement quantity must be at least 1")

    product = session.query(Product).filter(Product.id == target_product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Replacement product not found")

    shipment = rr.replacement
    will_deduct       = status in ("dispatched", "delivered")
    already_dispatched = shipment is not None and shipment.status in ("dispatched", "delivered")

    if will_deduct and not already_dispatched:
        if int(product.count or 0) < target_qty:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient stock for replacement: {int(product.count or 0)} available, {target_qty} required",
            )
        product.count = int(product.count or 0) - target_qty

    if shipment is None:
        shipment = ReplacementShipment(
            return_request_id=rr.id, product_id=target_product_id, quantity=target_qty,
            status=status, tracking_number=tracking_number,
        )
        session.add(shipment)
    else:
        shipment.product_id      = target_product_id
        shipment.quantity        = target_qty
        shipment.status          = status
        shipment.tracking_number = tracking_number

    if status in ("dispatched", "delivered") and shipment.dispatched_at is None:
        shipment.dispatched_at = _utcnow()
    if status == "delivered" and shipment.delivered_at is None:
        shipment.delivered_at = _utcnow()

    if rr.status != "replacement_dispatched":
        _transition(session, rr, "replacement_dispatched", actor_id=_as_uuid(admin_id),
                    note=note or "Replacement dispatched")

    session.commit()
    session.refresh(rr)
    return serialize_return_full(session, rr, include_order=True)