# app/orders/whatsapp.py
"""
WhatsApp + UPI orders (used while online payments are not live).

Flow:
  1. Customer checks out → POST /api/whatsapp-orders creates a real Order
     (status 'pending', payment 'whatsapp_upi' / 'unpaid'), prices everything
     server-side, reserves stock, emails the store and the customer, and returns
     the WhatsApp message the site opens for the customer to send to the store.
  2. The store confirms on WhatsApp, collects payment by UPI and marks it paid in
     Admin → PUT /api/admin/orders/{id}/payment, which also confirms the order.
  3. Cancelling an unshipped WhatsApp order gives the reserved stock back (once).
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.limiter import limiter
from app.orders.models import Order, OrderItem
from app.orders.services import create_order as svc_create_order, update_order_status_admin
from app.products.customization import describe as describe_customization
from app.products.models import Product
from app.users.models import Users
from app.users.utils import JWTBearer

logger = logging.getLogger(__name__)

whatsapp_order_router = APIRouter(prefix="/api/whatsapp-orders", tags=["Orders · WhatsApp"])
admin_payment_router = APIRouter(prefix="/api/admin/orders", tags=["Admin · Orders"])

PAYMENT_METHOD = "whatsapp_upi"
MAX_MESSAGE_ITEMS = 25


class WhatsAppOrderRequest(BaseModel):
    cart_items: List[dict] = Field(..., max_length=50)
    shipping_address: dict
    coupon_code:  Optional[str] = None
    gift_message: Optional[str] = None
    gift_to:      Optional[str] = None
    gift_from:    Optional[str] = None
    hide_prices:  bool = False


class PaymentUpdate(BaseModel):
    status: str = Field(..., pattern="^(paid|unpaid)$")
    reference: Optional[str] = Field(None, max_length=120)


def _inr(n: float) -> str:
    """Indian grouping: 248047 → ₹2,48,047."""
    s = str(int(round(n)))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return f"₹{s}"


def _clean(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def build_whatsapp_message(order_short: str, priced: dict, addr: dict, gift: dict) -> str:
    """The order message the customer sends to the store (server prices only)."""
    lines = ["Hi Little Loot! 🛍️ I'd like to place this order:", f"*Order #{order_short}*", ""]
    items = priced["items"]
    for i, it in enumerate(items[:MAX_MESSAGE_ITEMS], 1):
        name = _clean(it.get("name") or "Product", 80)
        colour = f" ({_clean(it.get('color'), 30)})" if it.get("color") else ""
        code = f" [{it['sku']}]" if it.get("sku") else ""
        qty = int(it.get("quantity", 1))
        lines.append(f"{i}. {name}{colour}{code} × {qty} — {_inr(float(it.get('price', 0)) * qty)}")
        if it.get("customization"):
            lines.append(f"   ✎ {describe_customization(it['customization'])}")
    if len(items) > MAX_MESSAGE_ITEMS:
        lines.append(f"…and {len(items) - MAX_MESSAGE_ITEMS} more item(s) (see order #{order_short})")
    lines.append("")
    lines.append(f"Subtotal: {_inr(priced['subtotal'])}")
    if priced["discount"]:
        code = priced.get("coupon_code")
        label = f"Discount ({code})" if code else "Discount"
        lines.append(f"{label}: −{_inr(priced['discount'])}")
    lines.append(f"Delivery: {_inr(priced['delivery']) if priced['delivery'] else 'FREE'}")
    lines.append(f"*Total: {_inr(priced['total'])}*")
    lines.append("")
    addr_bits = [addr.get("addressLine1"), addr.get("addressLine2"), addr.get("city"),
                 f"{_clean(addr.get('state'), 40)} - {_clean(addr.get('pincode'), 10)}"]
    lines.append(f"Deliver to: {_clean(addr.get('fullName'), 60)}")
    lines.append(_clean(", ".join(b for b in addr_bits if b and str(b).strip(' -')), 300))
    if addr.get("phone"):
        lines.append(f"Phone: {_clean(addr.get('phone'), 20)}")
    if gift.get("gift_message"):
        to_from = " · ".join(x for x in (f"To: {gift['gift_to']}" if gift.get("gift_to") else "",
                                        f"From: {gift['gift_from']}" if gift.get("gift_from") else "") if x)
        lines.append(f"🎁 Gift message{f' ({to_from})' if to_from else ''}: {_clean(gift['gift_message'], 150)}")
        if gift.get("hide_prices"):
            lines.append("   (please hide prices in the parcel)")
    lines.append("")
    lines.append("Please confirm the order and share the UPI details. Thank you!")
    return "\n".join(lines)


@whatsapp_order_router.post("", status_code=201)
@limiter.limit("10/minute")
def create_whatsapp_order(
    request: Request,
    body: WhatsAppOrderRequest,
    session: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    from app.payments.routers import price_cart
    from app.email.service import send_whatsapp_order_received, send_new_order_to_store

    if not user or not user.get("id"):
        raise HTTPException(status_code=401, detail="Please log in to place an order")
    uid = str(user["id"])

    addr = body.shipping_address or {}
    for field, label in (("fullName", "name"), ("phone", "phone number"), ("addressLine1", "address"),
                         ("city", "city"), ("state", "state"), ("pincode", "pincode")):
        if not str(addr.get(field) or "").strip():
            raise HTTPException(status_code=400, detail=f"Please add the delivery {label}")

    priced = price_cart(session, body.cart_items, body.coupon_code, user_id=user["id"])
    if priced["total"] <= 0:
        raise HTTPException(status_code=400, detail="Invalid order total")

    shipping_str = (f"{_clean(addr.get('fullName'), 80)}, {_clean(addr.get('addressLine1'), 200)}, "
                    f"{_clean(addr.get('city'), 60)}, {_clean(addr.get('state'), 60)} - {_clean(addr.get('pincode'), 10)}")
    gift = {
        "gift_message": (body.gift_message or "").strip()[:150] or None,
        "gift_to":      (body.gift_to or "").strip()[:24] or None,
        "gift_from":    (body.gift_from or "").strip()[:24] or None,
        "hide_prices":  bool(body.hide_prices),
    }
    order_data = {
        "shipping_address": shipping_str[:500],
        "total_amount":     priced["total"],
        "subtotal":         priced["subtotal"],
        "discount_amount":  priced["discount"],
        "delivery_fee":     priced["delivery"],
        "coupon_code":      priced["coupon_code"],
        **gift,
        "status":           "pending",
        "order_items": [{
            "product_id": it.get("product_id", ""),
            "quantity":   int(it.get("quantity", 1)),
            "price":      float(it.get("price", 0)),
            "color":      it.get("color") or None,
            "color_hex":  it.get("color_hex") or None,
            "image":      it.get("image") or None,
            "customization": it.get("customization") or None,
        } for it in priced["items"]],
    }

    try:
        db_order = svc_create_order(session, uid, order_data)   # also redeems the coupon
        db_order.payment_method = PAYMENT_METHOD
        db_order.payment_status = "unpaid"
        # Reserve stock now so the same units can't be sold twice while the store confirms
        for it in priced["items"]:
            p = session.query(Product).filter(Product.id == it.get("product_id")).first()
            if p:
                p.count = max(0, (p.count or 0) - int(it.get("quantity", 1)))
        session.commit()
        session.refresh(db_order)
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.error("WhatsApp order creation failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not place the order. Please try again.")

    order_short = str(db_order.id)[:8].upper()
    message = build_whatsapp_message(order_short, priced, addr, gift)

    customer = session.query(Users).filter(Users.id == uuid.UUID(uid)).first()
    email_items = [{"name": it.get("name") or "Product", "quantity": int(it.get("quantity", 1)),
                    "price": float(it.get("price", 0)), "customization": it.get("customization")}
                   for it in priced["items"]]
    if customer and customer.email:
        send_whatsapp_order_received(
            user_email=customer.email, user_name=customer.name or "", order_id=str(db_order.id),
            items=email_items, subtotal=priced["subtotal"], discount=priced["discount"],
            delivery=priced["delivery"], total=priced["total"], shipping_address=shipping_str,
            whatsapp_text=message,
        )
    send_new_order_to_store(
        order_id=str(db_order.id), customer_name=_clean(addr.get("fullName"), 80) or (customer.name if customer else ""),
        customer_phone=_clean(addr.get("phone"), 20) or (customer.phone if customer else ""),
        customer_email=customer.email if customer else "", items=email_items,
        total=priced["total"], shipping_address=shipping_str, message=message,
    )

    return {
        "order_id": str(db_order.id),
        "order_number": order_short,
        "total": priced["total"],
        "status": db_order.status,
        "payment_status": db_order.payment_status,
        "whatsapp_message": message,
    }


# ── Admin: record the UPI payment ─────────────────────────────────

@admin_payment_router.put("/{order_id}/payment")
def admin_set_payment(
    order_id: str,
    body: PaymentUpdate,
    session: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    from app.orders.routers import _send_status_email

    if not user or user.get("role") != 1:
        raise HTTPException(status_code=401, detail="Unauthorised")
    try:
        oid = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order id")
    order = session.query(Order).filter(Order.id == oid).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if (order.status or "").lower() == "cancelled" and body.status == "paid":
        raise HTTPException(status_code=409, detail="This order is cancelled — restore it before marking it paid")

    order.payment_method = order.payment_method or PAYMENT_METHOD
    order.payment_status = body.status
    order.payment_reference = (body.reference or "").strip() or order.payment_reference
    order.paid_at = datetime.now(timezone.utc) if body.status == "paid" else None
    session.commit()

    confirmed = False
    if body.status == "paid" and (order.status or "").lower() == "pending":
        order = update_order_status_admin(session, order_id, "confirmed")   # notifies the customer in-app
        _send_status_email(session, order, "confirmed")
        confirmed = True
    session.refresh(order)
    return {
        "id": str(order.id), "status": order.status, "payment_status": order.payment_status,
        "payment_reference": order.payment_reference,
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "confirmed": confirmed,
    }


def restore_stock_if_cancelled(session: Session, order: Order) -> int:
    """Give reserved stock back when an unpaid/unshipped WhatsApp order is cancelled.
    Runs once per order (stock_restored flag). Returns units restored."""
    if (order.status or "").lower() != "cancelled" or order.payment_method != PAYMENT_METHOD or order.stock_restored:
        return 0
    units = 0
    items = session.query(OrderItem).filter(OrderItem.order_id == order.id).all()
    for it in items:
        p = session.query(Product).filter(Product.id == it.product_id).first()
        if p:
            p.count = (p.count or 0) + int(it.quantity or 0)
            units += int(it.quantity or 0)
    order.stock_restored = True
    session.commit()
    return units
