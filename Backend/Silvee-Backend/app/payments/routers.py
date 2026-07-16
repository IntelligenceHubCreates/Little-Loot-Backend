# app/payments/router.py
import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import httpx
import razorpay
import resend
from fastapi import APIRouter, Depends, HTTPException, Request
from app.limiter import limiter
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.payments.models import PaymentOrder
from app.settings import settings
from app.users.utils import JWTBearer
from app.users.models import Users
from app.orders.models import Order, OrderItem
from app.orders.services import create_order as svc_create_order
from app.products.models import Product

logger = logging.getLogger(__name__)

payment_router = APIRouter(prefix="/api/payments", tags=["Payments"])


def get_razorpay_client() -> razorpay.Client:
    """Used only for local operations (signature verification). No network calls."""
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Payment service not configured.")
    return razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))


async def create_razorpay_order_async(payload: dict) -> dict:
    """Create a Razorpay order using async httpx — never blocks the event loop."""
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Payment service not configured.")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.razorpay.com/v1/orders",
                json=payload,
                auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            )
            if resp.status_code not in (200, 201):
                logger.error("Razorpay API error %s: %s", resp.status_code, resp.text)
                raise HTTPException(status_code=502, detail="Payment service error. Please try again.")
            return resp.json()
    except httpx.TimeoutException:
        logger.error("Razorpay API timed out")
        raise HTTPException(status_code=504, detail="Payment service timeout. Please try again.")
    except HTTPException:
        raise
    except Exception:
        logger.error("Razorpay order creation failed", exc_info=True)
        raise HTTPException(status_code=502, detail="Payment service error. Please try again.")


def _send_order_confirmation(user_email: str, user_name: str, order_id: str, total: float, items: list) -> None:
    """Send a branded order-confirmation email via Resend. Never raises."""
    if not settings.resend_api_key:
        return
    try:
        order_short = str(order_id)[:8].upper()
        items_html = "".join(
            f"<tr><td style='padding:8px 0;color:#5B4266;font-size:14px'>"
            f"{it.get('name', 'Product')} &times; {it.get('quantity', 1)}</td>"
            f"<td style='padding:8px 0;color:#3B0F4E;font-weight:700;font-size:14px;text-align:right'>"
            f"&#8377;{float(it.get('price', 0)) * int(it.get('quantity', 1)):.0f}</td></tr>"
            for it in items
        )
        resend.api_key = settings.resend_api_key
        resend.Emails.send({
            "from": f"Little Loot <{settings.resend_from_email}>",
            "to":   [user_email],
            "subject": f"Order confirmed! #{order_short} — Little Loot",
            "html": f"""
            <div style="font-family:sans-serif;max-width:520px;margin:0 auto;padding:32px 24px;background:#FFFDF9">
              <div style="text-align:center;margin-bottom:28px">
                <h1 style="font-size:24px;font-weight:800;color:#3B0F4E;margin:0">Little Loot</h1>
              </div>
              <div style="background:#fff;border-radius:16px;padding:28px 24px;border:1.5px solid #EFE7EC">
                <div style="text-align:center;margin-bottom:20px">
                  <h2 style="font-size:20px;font-weight:800;color:#3B0F4E;margin:8px 0 4px">Order Confirmed!</h2>
                  <p style="color:#8A7891;font-size:13px;margin:0">Order #{order_short}</p>
                </div>
                <p style="color:#5B4266;font-size:14px;line-height:1.7;margin:0 0 20px">
                  Hi {user_name or 'there'},<br>
                  Thank you for shopping with Little Loot! We are packing your order with love.
                </p>
                <table style="width:100%;border-collapse:collapse;margin-bottom:16px">
                  {items_html}
                  <tr style="border-top:1.5px solid #EFE7EC">
                    <td style="padding:12px 0 0;color:#3B0F4E;font-weight:800;font-size:16px">Total Paid</td>
                    <td style="padding:12px 0 0;color:#FF4D6A;font-weight:800;font-size:16px;text-align:right">
                      &#8377;{total:.0f}</td>
                  </tr>
                </table>
                <a href="{settings.frontend_url}/track-order"
                   style="display:block;text-align:center;background:linear-gradient(135deg,#FF4D6A,#E03A55);
                          color:#fff;font-weight:700;font-size:15px;padding:14px 24px;
                          border-radius:10px;text-decoration:none;margin-top:20px">
                  Track My Order &rarr;
                </a>
              </div>
              <p style="text-align:center;color:#8A7891;font-size:11px;margin-top:20px">
                Questions? Contact us at support@littleloot.in<br>
                &copy; Little Loot &mdash; gifts that spark joy
              </p>
            </div>
            """,
        })
    except Exception:
        logger.warning("Order confirmation email failed for order %s", order_id, exc_info=False)


# ── Schemas ───────────────────────────────────────────────────────────────

class CreateOrderRequest(BaseModel):
    amount: int
    cart_items: List[dict]
    shipping_address: dict
    coupon_code: Optional[str] = None      # ← NEW
    gift_message: Optional[str] = None     # ← NEW
    notes: Optional[dict] = None

class CreateOrderResponse(BaseModel):
    razorpay_order_id: str
    amount: int
    currency: str
    key_id: str

class VerifyPaymentRequest(BaseModel):
    razorpay_order_id:   str
    razorpay_payment_id: str
    razorpay_signature:  str

class VerifyPaymentResponse(BaseModel):
    success:    bool
    payment_id: str
    order_id:   str
    message:    str


# ── Endpoints ─────────────────────────────────────────────────────────────

@payment_router.post("/create-order", response_model=CreateOrderResponse)
@limiter.limit("10/minute")
async def create_payment_order(
    request: Request,
    body: CreateOrderRequest,
    session: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    import math
    from app.coupons.services import evaluate_coupon, CouponError

    DELIVERY_FEE            = 49.0
    FREE_DELIVERY_THRESHOLD = 499.0

    if not body.cart_items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    # ── Recompute subtotal from REAL product prices (never trust client) ──
    subtotal = 0.0
    for item in body.cart_items:
        product = session.query(Product).filter(Product.id == item.get("product_id")).first()
        if not product:
            raise HTTPException(status_code=404, detail=f"Product not found: {item.get('product_id')}")
        if not getattr(product, "is_active", True):
            raise HTTPException(status_code=400, detail=f"{product.name} is no longer available")
        qty = int(item.get("quantity", 1))
        if qty < 1:
            raise HTTPException(status_code=400, detail="Invalid quantity")
        if product.count < qty:
            raise HTTPException(status_code=400, detail=f"Only {product.count} left for {product.name}")

        orig = float(product.original_price or 0)
        amt  = float(getattr(product, "amount_discount", 0) or 0)
        pct  = float(getattr(product, "percentage_discount", 0) or 0)
        if amt > 0:   unit = orig - amt
        elif pct > 0: unit = float(math.floor(orig - orig * pct / 100 + 0.5))
        else:         unit = orig
        subtotal += max(0.0, unit) * qty

    # ── Coupon (server-validated) ──
    discount = 0.0
    coupon_code_norm = None
    if body.coupon_code:
        try:
            coupon_obj, discount = evaluate_coupon(session, body.coupon_code, subtotal)
            coupon_code_norm = coupon_obj.code
        except CouponError as ce:
            raise HTTPException(status_code=400, detail=f"Coupon: {ce.message}")

    delivery = 0.0 if subtotal >= FREE_DELIVERY_THRESHOLD else DELIVERY_FEE
    total_rupees = max(0.0, subtotal - discount + delivery)
    amount_paise = int(round(total_rupees * 100))

    if amount_paise <= 0:
        raise HTTPException(status_code=400, detail="Invalid order total")

    rz_order = await create_razorpay_order_async({
        "amount": amount_paise, "currency": "INR",
        "receipt": f"rcpt_{uuid.uuid4().hex[:12]}",
        "payment_capture": 1,
        "notes": {"source": "LittleLoot"},
    })

    # Snapshot everything verify needs to build the real order
    snapshot = {
        "items":        body.cart_items,
        "coupon_code":  coupon_code_norm,
        "gift_message": (body.gift_message or "").strip()[:500] or None,
        "subtotal":     subtotal,
        "discount":     discount,
        "delivery":     delivery,
        "total":        total_rupees,
    }

    session.add(PaymentOrder(
        user_id=uuid.UUID(str(user["id"])) if user and user.get("id") else None,
        razorpay_order_id=rz_order["id"],
        amount=amount_paise,
        currency="INR",
        status="created",
        cart_snapshot=snapshot,                 # ← now structured, not just items
        shipping_address=body.shipping_address,
    ))
    session.commit()

    return CreateOrderResponse(
        razorpay_order_id=rz_order["id"], amount=amount_paise,
        currency="INR", key_id=settings.razorpay_key_id,
    )

@payment_router.post("/verify", response_model=VerifyPaymentResponse)
async def verify_payment(
    body: VerifyPaymentRequest,
    session: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    # SECURITY: Verify the payment order belongs to the authenticated user.
    # Without this check any logged-in user can verify/claim another user's order.
    user_id = str(user["id"]) if user and user.get("id") else None
    order = session.query(PaymentOrder).filter(
        PaymentOrder.razorpay_order_id == body.razorpay_order_id,
        PaymentOrder.user_id           == uuid.UUID(user_id) if user_id else False,
    ).first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.is_verified:
        return VerifyPaymentResponse(
            success=True,
            payment_id=order.razorpay_payment_id,
            order_id=str(order.id),
            message="Already verified",
        )

    expected = hmac.new(
        settings.razorpay_key_secret.encode(),
        f"{body.razorpay_order_id}|{body.razorpay_payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, body.razorpay_signature):
        order.status = "failed"
        session.commit()
        raise HTTPException(status_code=400, detail="Invalid payment signature.")

    order.razorpay_payment_id = body.razorpay_payment_id
    order.razorpay_signature  = body.razorpay_signature
    order.status              = "paid"
    order.is_verified         = True
    order.paid_at             = datetime.now(timezone.utc)
    session.commit()

    # ── Create the real Order exactly once (idempotent on payment id) ──
    existing = session.query(Order).filter(
        Order.razorpay_payment_id == body.razorpay_payment_id
    ).first()
    if existing:
        return VerifyPaymentResponse(success=True, payment_id=body.razorpay_payment_id,
                                     order_id=str(existing.id), message="Already created")

    snap  = order.cart_snapshot or {}
    items = snap.get("items", []) if isinstance(snap, dict) else (snap or [])
    addr  = order.shipping_address or {}
    uid   = str(order.user_id) if order.user_id else None

    order_items_data = []
    for it in items:
        order_items_data.append({
            "product_id": it.get("product_id", ""),
            "quantity":   int(it.get("quantity", 1)),
            "price":      float(it.get("price", 0)),   # display; server total already authoritative
            "color":      it.get("color")     or None,
            "color_hex":  it.get("color_hex") or None,
            "image":      it.get("image")     or None,
        })

    shipping_str = (f"{addr.get('fullName','')}, {addr.get('addressLine1','')}, "
                    f"{addr.get('city','')}, {addr.get('state','')} - {addr.get('pincode','')}")

    order_data = {
        "shipping_address": shipping_str,
        "total_amount":     float(snap.get("total", order.amount / 100)),  # server total
        "subtotal":         snap.get("subtotal"),
        "discount_amount":  snap.get("discount") or 0,
        "delivery_fee":     snap.get("delivery") or 0,
        "coupon_code":      snap.get("coupon_code"),
        "gift_message":     snap.get("gift_message"),
        "status":           "confirmed",
        "order_items":      order_items_data,
    }

    try:
        db_order = svc_create_order(session, uid, order_data)  # this now redeems coupon (see note)
        db_order.razorpay_payment_id = body.razorpay_payment_id
        # ── Deduct stock, once, in the paid transaction ──
        for it in items:
            p = session.query(Product).filter(Product.id == it.get("product_id")).first()
            if p:
                p.count = max(0, (p.count or 0) - int(it.get("quantity", 1)))
        session.commit()
    except Exception:
        logger.error("Order creation failed after payment verify", exc_info=True)
        raise HTTPException(status_code=500, detail="Order creation failed. Contact support.")

    # ── Order confirmation email (fire-and-forget; never blocks the response) ──
    if uid:
        customer = session.query(Users).filter(Users.id == uuid.UUID(uid)).first()
        if customer:
            _send_order_confirmation(
                user_email=customer.email,
                user_name=customer.name or "",
                order_id=str(db_order.id),
                total=float(snap.get("total", order.amount / 100)),
                items=items,
            )

    return VerifyPaymentResponse(
        success=True, payment_id=body.razorpay_payment_id,
        order_id=str(db_order.id), message="Payment verified & order created",
    )


@payment_router.get("/order/{razorpay_order_id}")
async def get_order_status(
    razorpay_order_id: str,
    session: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    user_id = str(user["id"]) if user and user.get("id") else None
    order = session.query(PaymentOrder).filter(
        PaymentOrder.razorpay_order_id == razorpay_order_id,
        PaymentOrder.user_id           == uuid.UUID(user_id) if user_id else False,
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {
        "status":              order.status,
        "is_verified":         order.is_verified,
        "amount":              order.amount / 100,
        "razorpay_payment_id": order.razorpay_payment_id,
        "paid_at":             order.paid_at,
    }


@payment_router.post("/webhook")
async def razorpay_webhook(request: Request, session: Session = Depends(get_db)):
    """
    SECURITY: Razorpay signs every webhook with HMAC-SHA256 using the webhook
    secret.  We MUST verify this signature before trusting any event — otherwise
    any attacker can POST a fake `payment.captured` event and mark unpaid orders
    as paid without spending a rupee.
    """
    import json as _json

    razorpay_signature = request.headers.get("X-Razorpay-Signature", "")
    if not razorpay_signature:
        # Reject unsigned webhook calls immediately.
        raise HTTPException(status_code=400, detail="Missing webhook signature")

    raw_body = await request.body()

    expected = hmac.new(
        settings.razorpay_webhook_secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, razorpay_signature):
        # Signature mismatch — could be a tampered or spoofed request.
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    try:
        request_body = _json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event    = request_body.get("event", "")
    entity   = request_body.get("payload", {}).get("payment", {}).get("entity", {})
    order_id = entity.get("order_id")
    if not order_id:
        return {"status": "ok"}

    order = session.query(PaymentOrder).filter(
        PaymentOrder.razorpay_order_id == order_id
    ).first()
    if order:
        if event == "payment.captured" and not order.is_verified:
            order.status              = "paid"
            order.razorpay_payment_id = entity.get("id")
            order.is_verified         = True
            order.paid_at             = datetime.now(timezone.utc)
        elif event == "payment.failed":
            order.status = "failed"
        session.commit()
    return {"status": "ok"}


# ── Create order after payment ────────────────────────────────────────────

# @payment_router.post("/create-order-after-payment/{razorpay_order_id}")
# async def create_order_after_payment(
#    razorpay_order_id: str,
#    session: Session = Depends(get_db),
#    user=Depends(JWTBearer()),
#):
#    payment = session.query(PaymentOrder).filter(
#        PaymentOrder.razorpay_order_id == razorpay_order_id,
#        PaymentOrder.is_verified == True,
#    ).first()

#    if not payment:
#        raise HTTPException(status_code=404, detail="Verified payment not found")

    # Idempotent check — column now exists (migration 004).
#    existing = session.query(Order).filter(
#        Order.razorpay_payment_id == payment.razorpay_payment_id
#    ).first()
#    if existing:
#        return _format_order(existing)

#    addr    = payment.shipping_address or {}
#    items   = payment.cart_snapshot    or []
#    user_id = str(user["id"]) if user and user.get("id") else None

#    order_items_data = []
#    total_amount     = 0

#    for item in items:
#        price    = float(item.get("price",    0))
#        quantity = int(item.get("quantity",   1))
#        total_amount += price * quantity

#        order_items_data.append({
#            "product_id": item.get("product_id", ""),
#            "quantity":   quantity,
#            "price":      price,
            # FIX: pass color fields from cart_snapshot into the order item
            # cart_snapshot was saved with full payload including color/color_hex/image
#            "color":      item.get("color")     or None,
#            "color_hex":  item.get("color_hex") or None,
#            "image":      item.get("image")     or None,
#        })

#    shipping_str = (
#        f"{addr.get('fullName',     '')}, "
#        f"{addr.get('addressLine1', '')}, "
#        f"{addr.get('city',         '')}, "
#        f"{addr.get('state',        '')} - "
#        f"{addr.get('pincode',      '')}"
#   )

#    order_data = {
#        "shipping_address": shipping_str,
#        "total_amount":     payment.amount / 100,
#        "payment_method":   "Razorpay",
#        "payment_status":   "paid",
#        "status":           "confirmed",
#        "order_items":      order_items_data,   # now includes color/color_hex/image
#    }

#    try:
#        db_order = svc_create_order(session, user_id, order_data)
#    except Exception as e:
#        raise HTTPException(status_code=500, detail=f"Order creation failed: {str(e)}")

    # Link order → payment so the admin panel shows "Paid".
#    db_order.razorpay_payment_id = payment.razorpay_payment_id
#    session.commit()
#    session.refresh(db_order)

#    return _format_order(db_order)

@payment_router.get("/order-by-payment/{razorpay_payment_id}")
async def get_order_by_payment(
    razorpay_payment_id: str,
    session: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    user_id = str(user["id"]) if user and user.get("id") else None

    if hasattr(Order, 'razorpay_payment_id'):
        q = session.query(Order).filter(
            Order.razorpay_payment_id == razorpay_payment_id
        )
        if user_id and hasattr(Order, 'user_id'):
            q = q.filter(Order.user_id == user_id)
        order = q.first()
        if order:
            return _format_order(order)

    q2 = session.query(PaymentOrder).filter(
        PaymentOrder.razorpay_payment_id == razorpay_payment_id
    )
    if user_id:
        try:
            q2 = q2.filter(PaymentOrder.user_id == uuid.UUID(user_id))
        except (ValueError, AttributeError):
            pass
    payment = q2.first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    addr = payment.shipping_address or {}
    return {
        "id":                    str(payment.id),
        "status":                "confirmed" if payment.is_verified else "pending",
        "amount_paid":           payment.amount / 100,
        "payment_method":        "Razorpay",
        "razorpay_payment_id":   payment.razorpay_payment_id,
        "shipping_name":         addr.get("fullName", ""),
        "shipping_city":         addr.get("city", ""),
        "items":                 payment.cart_snapshot or [],
        "created_at":            payment.created_at.isoformat() if payment.created_at else None,
        "tracking_events":       [],
    }


def _format_order(order: Order) -> dict:
    items = []
    for oi in getattr(order, 'order_items', []):
        items.append({
            "product_id": str(getattr(oi, 'product_id', '')),
            "name":       getattr(oi.product, 'name', 'Product') if hasattr(oi, 'product') and oi.product else 'Product',
            "price":      float(getattr(oi, 'price', 0)),
            "quantity":   int(getattr(oi, 'quantity', 1)),
            "color":      getattr(oi, 'color',     None),
            "color_hex":  getattr(oi, 'color_hex', None),
        })

    addr = getattr(order, 'shipping_address', '') or ''
    return {
        "id":                    str(order.id),
        "status":                getattr(order, 'status', 'confirmed'),
        "amount_paid":           float(getattr(order, 'total_amount', 0)),
        "payment_method":        getattr(order, 'payment_method', 'Razorpay'),
        "razorpay_payment_id":   getattr(order, 'razorpay_payment_id', None),
        "shipping_name":         addr.split(',')[0].strip() if addr else '',
        "shipping_city":         addr.split(',')[-2].strip() if ',' in addr else '',
        "shipping_state":        '',
        "shipping_address":      addr,
        "shipping_pincode":      addr.split('-')[-1].strip() if '-' in addr else '',
        "shipping_phone":        '',
        "courier_name":          getattr(order, 'courier_name', None),
        "awb_number":            getattr(order, 'awb_number', None),
        "items":                 items,
        "created_at":            order.created_at.isoformat() if order.created_at else None,
        "estimated_delivery":    None,
        "delivered_at":          getattr(order, 'delivered_at', None),
        "tracking_events":       getattr(order, 'tracking_events', []) or [],
    }