# app/orders/router.py
# Only create_order is changed — color fields are now copied from the
# frontend payload into enriched_items and saved via services.create_order.
# Every other endpoint is identical to your original.

import logging
from fastapi import APIRouter, Depends, HTTPException, status, Request

logger = logging.getLogger(__name__)
from sqlalchemy.orm import Session, joinedload, selectinload
from app.db import get_db
from app.orders.models import Order, OrderItem
from app.orders.schemas import OrderCreate, OrderResponse, OrderItemResponse
from app.users.models import Users
from app.products.models import Product
from app.users.utils import get_current_user, JWTBearer
from typing import List, Optional
import uuid
from pydantic import BaseModel
from datetime import datetime, timedelta
from app.orders.services import (
    create_order, get_user_orders, get_order, update_order,
    get_order_items, get_all_orders_admin, update_order_status_admin,
)
from app.email.service import (
    send_order_shipped,
    send_order_delivered,
    send_order_cancelled,
)
from app.products.services import get_product
from sqlalchemy import func, extract, or_, cast, String
from datetime import datetime, timedelta
from app.users.models import Users

router = APIRouter(prefix="/api", tags=["orders"])

# near the top of app/orders/router.py
from app.coupons.services import evaluate_coupon, CouponError
import math

# ⚠️ These MUST match your cart (CartPage: DELIVERY_FEE / FREE_DELIVERY_THRESHOLD)
DELIVERY_FEE            = 49.0
FREE_DELIVERY_THRESHOLD = 499.0


# ── Dashboard endpoints (unchanged) ──────────────────────────────

@router.get("/admin/dashboard/kpis")
async def dashboard_kpis(
    db: Session = Depends(get_db),
    user = Depends(JWTBearer())
):
    """Dashboard KPI cards — revenue, orders, customers, return rate"""
    if user is None or user['role'] != 1:
        raise HTTPException(status_code=401, detail="Unauthorised")

    now         = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0)
    prev_start  = (month_start - timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0)

    this_rev = db.query(func.sum(Order.total_amount)).filter(
        Order.created_at >= month_start, Order.status != 'cancelled'
    ).scalar() or 0

    prev_rev = db.query(func.sum(Order.total_amount)).filter(
        Order.created_at >= prev_start,
        Order.created_at < month_start,
        Order.status != 'cancelled'
    ).scalar() or 0

    this_orders = db.query(func.count(Order.id)).filter(
        Order.created_at >= month_start
    ).scalar() or 0

    prev_orders = db.query(func.count(Order.id)).filter(
        Order.created_at >= prev_start,
        Order.created_at < month_start
    ).scalar() or 0

    total_customers = db.query(func.count(Users.id)).scalar() or 0
    total_orders    = db.query(func.count(Order.id)).filter(
        Order.created_at >= month_start
    ).scalar() or 1

    returns = db.query(func.count(Order.id)).filter(
        Order.created_at >= month_start,
        Order.status == 'returned'
    ).scalar() or 0

    def trend(a, b):
        if b == 0: return 0.0
        return round((float(a) - float(b)) / float(b) * 100, 1)

    def fmt_inr(amt):
        r = float(amt)
        if r >= 100000: return f"₹{r/100000:.2f}L"
        if r >= 1000:   return f"₹{r:,.0f}"
        return f"₹{r:.0f}"

    return {
        "revenue":   {"value": float(this_rev), "formatted": fmt_inr(this_rev), "trend": trend(this_rev, prev_rev)},
        "orders":    {"total": this_orders, "trend": trend(this_orders, prev_orders)},
        "customers": {"total": total_customers, "trend": 0.0},
        "returns":   {"rate": round(returns / total_orders * 100, 1), "trend": 0.0}
    }


@router.get("/admin/analytics")
async def analytics_chart(
    range: str = "6M",
    db: Session = Depends(get_db),
    user = Depends(JWTBearer())
):
    """Revenue vs Orders bar chart data"""
    if user is None or user['role'] != 1:
        raise HTTPException(status_code=401, detail="Unauthorised")

    from sqlalchemy import text as _text

    now   = datetime.utcnow()
    days  = {"7D": 7, "6M": 183, "1Y": 365}.get(range, 183)
    since = now - timedelta(days=days)
    # SECURITY: trunc_val is derived from a closed allowlist, not from user input.
    # Using a parameterised query with SQLAlchemy text() prevents accidental SQL
    # injection if this logic is ever extended to pass user-controlled values.
    trunc_map = {"7D": "day", "6M": "month", "1Y": "month"}
    trunc_val = trunc_map.get(range, "month")

    rows = db.execute(
        _text("""SELECT date_trunc(:trunc, created_at) AS period,
               COALESCE(SUM(total_amount), 0) AS revenue,
               COUNT(*) AS orders
            FROM orders
            WHERE created_at >= :since AND status != 'cancelled'
            GROUP BY 1 ORDER BY 1 ASC"""),
        {"trunc": trunc_val, "since": since}
    ).fetchall()

    if not rows:
        return []

    max_rev = max(r.revenue for r in rows) or 1
    max_ord = max(r.orders  for r in rows) or 1
    fmt     = "%d %b" if range == "7D" else ("%b" if range == "6M" else "%b '%y")

    return [
        {
            "label":   r.period.strftime(fmt),
            "revenue": round(float(r.revenue) / float(max_rev) * 100),
            "orders":  round(r.orders / max_ord * 100),
        }
        for r in rows
    ]


@router.get("/admin/order-status")
async def order_status_donut(
    db: Session = Depends(get_db),
    user = Depends(JWTBearer())
):
    """Donut chart — order status breakdown for current month"""
    if user is None or user['role'] != 1:
        raise HTTPException(status_code=401, detail="Unauthorised")

    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0)
    rows = db.query(Order.status, func.count(Order.id).label("cnt")).filter(
        Order.created_at >= month_start
    ).group_by(Order.status).all()

    total  = sum(r.cnt for r in rows) or 1
    colors = {
        "processing": "var(--sun)",  "delivered":  "var(--mint)",
        "pending":    "var(--coral)","cancelled":  "var(--lilac)",
        "returned":   "var(--sky)"
    }
    return {
        "total": total,
        "breakdown": [
            {
                "label":      r.status.capitalize(),
                "count":      r.cnt,
                "percentage": round(r.cnt / total * 100),
                "color":      colors.get(r.status.lower(), "var(--border)"),
            }
            for r in sorted(rows, key=lambda x: -x.cnt)
        ]
    }


@router.get("/admin/orders/recent")
async def recent_orders_dashboard(
    db: Session = Depends(get_db),
    user = Depends(JWTBearer())
):
    """Last 5 orders for dashboard mini-table"""
    if user is None or user['role'] != 1:
        raise HTTPException(status_code=401, detail="Unauthorised")

    from sqlalchemy.orm import joinedload
    orders = db.query(Order).options(joinedload(Order.user)).order_by(
        Order.created_at.desc()
    ).limit(5).all()

    def fmt_inr(amt):
        r = float(amt)
        if r >= 100000: return f"₹{r/100000:.2f}L"
        if r >= 1000:   return f"₹{r:,.0f}"
        return f"₹{r:.0f}"

    return {
        "orders": [
            {
                "id":               str(o.id),
                "customer_name":    o.user.name if o.user else "Unknown",
                "customer_city":    o.shipping_address.split(",")[-1].strip()[:20] if o.shipping_address else "",
                "items_count":      len(o.order_items),
                "amount":           float(o.total_amount),
                "amount_formatted": fmt_inr(o.total_amount),
                "payment_method":   "UPI",
                "date":             o.created_at.isoformat(),
                "date_formatted":   o.created_at.strftime("%-d %b %Y"),
                "status":           o.status.capitalize(),
            }
            for o in orders
        ]
    }

# ── Canonical admin status set (Phase 4) ──────────────────────────
ADMIN_ALLOWED_STATUSES = {"pending", "confirmed", "processing", "shipped", "delivered", "cancelled"}


def _serialize_admin_order(o, payment_map: dict) -> dict:
    """Hand-built admin order dict. Kept separate from OrderResponse so
    customer-facing endpoints are never polluted with admin/payment fields."""
    items = []
    for it in (getattr(o, "order_items", []) or []):
        prod = getattr(it, "product", None)
        items.append({
            "product_id": str(getattr(it, "product_id", "") or ""),
            "name":       (getattr(prod, "name", None) if prod else None) or "—",
            "qty":        int(getattr(it, "quantity", 1) or 1),
            "price":      float(getattr(it, "price", 0) or 0),
            "color":      getattr(it, "color", None),
            "color_hex":  getattr(it, "color_hex", None),
        })

    u    = getattr(o, "user", None)
    rpid = getattr(o, "razorpay_payment_id", None)
    pay  = payment_map.get(rpid) if rpid else None

    return {
        "id":             str(o.id),
        "order_number":   str(o.id)[:8].upper(),
        "user_id":        str(o.user_id or ""),
        "customer_name":  (getattr(u, "name", None)  or "") if u else "",
        "customer_email": (getattr(u, "email", None) or "") if u else "",
        "customer_phone": (getattr(u, "phone", None) or "") if u else "",
        "shipping_address": getattr(o, "shipping_address", "") or "",
        "status":         getattr(o, "status", "pending") or "pending",
        "total_amount":   float(getattr(o, "total_amount", 0) or 0),
        "created_at":     o.created_at.isoformat() if getattr(o, "created_at", None) else "",
        "updated_at":     o.updated_at.isoformat() if getattr(o, "updated_at", None) else "",
        # Payment — real Razorpay status where a verified record links, else empty.
        "payment_status": (pay.status if pay else ""),
        "payment_method": ("Razorpay" if pay else ""),
        "razorpay_payment_id": rpid,
        "paid_at":        (pay.paid_at.isoformat() if pay and pay.paid_at else None),
        "items":          items,
    }


def _serialize_order_items_for_email(order) -> list:
    """Build item dicts for email templates from an Order ORM object."""
    result = []
    for oi in (getattr(order, "order_items", []) or []):
        prod = getattr(oi, "product", None)
        result.append({
            "name":     (getattr(prod, "name", None) if prod else None) or "Product",
            "quantity": int(getattr(oi, "quantity", 1) or 1),
            "price":    float(getattr(oi, "price", 0) or 0),
        })
    return result


def _send_status_email(db, order, new_status: str) -> None:
    """Dispatch the right lifecycle email for an order status change."""
    try:
        customer = db.query(Users).filter(Users.id == order.user_id).first()
        if not customer:
            return
        items      = _serialize_order_items_for_email(order)
        order_id   = str(order.id)
        total      = float(getattr(order, "total_amount", 0) or 0)
        name       = customer.name or ""
        email      = customer.email

        if new_status == "shipped":
            send_order_shipped(
                user_email=email, user_name=name,
                order_id=order_id, items=items, total=total,
            )
        elif new_status == "delivered":
            send_order_delivered(
                user_email=email, user_name=name,
                order_id=order_id, items=items, total=total,
            )
        elif new_status == "cancelled":
            send_order_cancelled(
                user_email=email, user_name=name,
                order_id=order_id, total=total, cancelled_by="admin",
            )
    except Exception:
        logger.warning("Status-change email failed for order %s → %s", order.id, new_status, exc_info=True)


@router.get("/admin/orders")
async def get_all_orders(
    skip: int = 0,
    limit: int = 15,
    status: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sortBy: str = "created_at",
    sortDir: str = "desc",
    db: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    if user is None or user["role"] != 1:
        raise HTTPException(status_code=401, detail="Unauthorised")

    from app.payments.routers import PaymentOrder
    from sqlalchemy.orm import contains_eager

    q = db.query(Order)

    # Filters applied BEFORE count so pagination is correct
    if search:
        q = q.join(Order.user).filter(
            or_(
                Users.name.ilike(f"%{search}%"),
                Users.email.ilike(f"%{search}%"),
                cast(Order.id, String).ilike(f"{search}%"),
            )
        )
    if status and status.lower() != "all":
        q = q.filter(Order.status.ilike(status))
    if date_from:
        try:
            q = q.filter(Order.created_at >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(Order.created_at < datetime.fromisoformat(date_to) + timedelta(days=1))
        except ValueError:
            pass

    total = q.count()   # AFTER filters

    col      = Order.total_amount if sortBy == "total_amount" else Order.created_at
    order_by = col.asc() if sortDir == "asc" else col.desc()

    orders_q = q.options(selectinload(Order.order_items).joinedload(OrderItem.product))
    orders_q = orders_q.options(contains_eager(Order.user)) if search else orders_q.options(joinedload(Order.user))

    orders = orders_q.order_by(order_by).offset(skip).limit(limit).all()

    rpids = [r for r in (getattr(o, "razorpay_payment_id", None) for o in orders) if r]
    payment_map: dict = {}
    if rpids:
        for p in db.query(PaymentOrder).filter(PaymentOrder.razorpay_payment_id.in_(rpids)).all():
            payment_map[p.razorpay_payment_id] = p

    return {
        "data":       [_serialize_admin_order(o, payment_map) for o in orders],
        "totalCount": total,
        "page":       (skip // limit) + 1 if limit else 1,
        "limit":      limit,
    }
@router.put("/admin/orders/{order_id}")
async def admin_update_order_status(
    order_id: str,
    status_update: dict,
    db: Session = Depends(get_db),
    user = Depends(JWTBearer()),
):
    """Update order status (Admin only). Validates against the canonical set."""
    if user is None or user["role"] != 1:
        raise HTTPException(status_code=401, detail="Unauthorised")

    new_status = (status_update.get("status") or "").lower().strip()
    if new_status not in ADMIN_ALLOWED_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Allowed: {', '.join(sorted(ADMIN_ALLOWED_STATUSES))}",
        )

    order = update_order_status_admin(db, order_id, new_status)  # stores lowercase
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # ── Lifecycle email notifications (fire-and-forget) ──────────────────────
    _send_status_email(db, order, new_status)

    return {"id": str(order.id), "status": order.status}

# ── CREATE ORDER — color fields copied from payload ───────────────

@router.post("/orders", response_model=OrderResponse)
async def create_new_order(
    order: OrderCreate,
    db: Session = Depends(get_db),
    user = Depends(JWTBearer())
):
    """Create a new order — total is computed server-side from real product
    discounts + coupon + delivery. Client-sent totals are ignored."""
    try:
        subtotal       = 0.0
        enriched_items = []

        for item in order.order_items:
            product = get_product(db, item.product_id)
            if not product:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Product {item.product_id} not found",
                )
            if not getattr(product, "is_active", True):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{product.name} is no longer available",
                )
            if product.count < item.quantity:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Only {product.count} left in stock for {product.name}",
                )

            # Discounted unit price — mirrors the cart exactly:
            # amount discount wins, else percentage; round-half-up like JS Math.round;
            # never below 0.  (Previously this used original_price = full MRP.)
            orig = float(product.original_price or 0)
            amt  = float(getattr(product, "amount_discount", 0) or 0)
            pct  = float(getattr(product, "percentage_discount", 0) or 0)
            if amt > 0:
                unit = orig - amt
            elif pct > 0:
                unit = float(math.floor(orig - orig * pct / 100 + 0.5))
            else:
                unit = orig
            if unit < 0:
                unit = 0.0

            subtotal += unit * item.quantity

            item_data          = item.model_dump()
            item_data["price"] = unit          # store the ACTUAL charged unit price
            enriched_items.append(item_data)

        # ── Coupon: re-validate server-side (never trust a client amount) ──
        coupon_obj      = None
        discount_amount = 0.0
        if getattr(order, "coupon_code", None):
            try:
                coupon_obj, discount_amount = evaluate_coupon(db, order.coupon_code, subtotal)
            except CouponError as ce:
                raise HTTPException(status_code=400, detail=f"Coupon: {ce.message}")

        # ── Delivery: same rule as the cart, so the stored total matches ──
        delivery_fee = 0.0 if subtotal >= FREE_DELIVERY_THRESHOLD else DELIVERY_FEE

        total_amount = max(0.0, subtotal - discount_amount + delivery_fee)

        order_data                    = order.model_dump()
        order_data["subtotal"]        = subtotal
        order_data["discount_amount"] = discount_amount
        order_data["delivery_fee"]    = delivery_fee
        order_data["coupon_code"]     = coupon_obj.code if coupon_obj else None
        order_data["total_amount"]    = total_amount
        order_data["order_items"]     = enriched_items
        order_data["gift_message"] = order.model_dump().get("gift_message")

        user_id  = user.get('id')
        db_order = create_order(db, user_id, order_data)
        return db_order

    except HTTPException:
        raise
    except Exception:
        logger.error("create_new_order failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Order creation failed")

# ── Remaining endpoints (unchanged) ──────────────────────────────

@router.get("/orders", response_model=List[OrderResponse])
async def get_orders(
    db: Session = Depends(get_db),
    user = Depends(JWTBearer())
):
    """Get all orders for the current user"""
    try:
        orders = get_user_orders(db, user.get('id', ''))
        result = []
        for i in orders:
            if isinstance(i.id,      uuid.UUID): i.id      = str(i.id)
            if isinstance(i.user_id, uuid.UUID): i.user_id = str(i.user_id)
            for item in i.order_items:
                if isinstance(item.id,         uuid.UUID): item.id         = str(item.id)
                if isinstance(item.order_id,   uuid.UUID): item.order_id   = str(item.order_id)
                if isinstance(item.product_id, uuid.UUID): item.product_id = str(item.product_id)
                if isinstance(item.product.id, uuid.UUID): item.product.id = str(item.product.id)
            result.append(i)
        return [OrderResponse.from_orm(order) for order in result]
    except Exception:
        logger.error("get_orders failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve orders")


@router.get("/orders/{order_id}", response_model=OrderResponse)
async def get_order_by_id(
    order_id: str,
    db: Session = Depends(get_db),
    user = Depends(JWTBearer())
):
    """Get order by ID"""
    try:
        user_id = user.get('id')
        order   = get_order(db, user_id, order_id)
        if not order:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
        if isinstance(order.id,      uuid.UUID): order.id      = str(order.id)
        if isinstance(order.user_id, uuid.UUID): order.user_id = str(order.user_id)
        for item in order.order_items:
            if isinstance(item.id,         uuid.UUID): item.id         = str(item.id)
            if isinstance(item.order_id,   uuid.UUID): item.order_id   = str(item.order_id)
            if isinstance(item.product_id, uuid.UUID): item.product_id = str(item.product_id)
            if isinstance(item.product.id, uuid.UUID): item.product.id = str(item.product.id)
        return order
    except HTTPException:
        raise
    except Exception:
        logger.error("get_order_by_id failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve order")


@router.put("/orders/{order_id}", response_model=OrderResponse)
async def update_order_status(
    order_id: str,
    status: str,
    db: Session = Depends(get_db),
    user = Depends(JWTBearer())
):
    """Update order status"""
    try:
        user_id = user.get('id')
        order   = update_order(db, user_id, order_id, status)
        if not order:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
        if isinstance(order.id,      uuid.UUID): order.id      = str(order.id)
        if isinstance(order.user_id, uuid.UUID): order.user_id = str(order.user_id)
        return order
    except HTTPException:
        raise
    except Exception:
        logger.error("update_order_status failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update order")


@router.patch("/orders/{order_id}/cancel")
async def cancel_user_order(
    order_id: str,
    db: Session = Depends(get_db),
    user = Depends(JWTBearer()),
):
    """Cancel an order — user-facing. Only cancellable while still pending / processing / confirmed."""
    user_id = user.get("id")
    try:
        db_order = db.query(Order).filter(
            Order.id      == order_id,
            Order.user_id == user_id,
        ).first()

        if not db_order:
            raise HTTPException(status_code=404, detail="Order not found")

        if db_order.status.lower() not in ("pending", "processing", "confirmed"):
            raise HTTPException(
                status_code=400,
                detail=f"Order cannot be cancelled (current status: {db_order.status})",
            )

        db_order.status     = "cancelled"
        db_order.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(db_order)

        # ── Cancellation email (fire-and-forget) ─────────────────────────────
        try:
            customer = db.query(Users).filter(Users.id == db_order.user_id).first()
            if customer:
                send_order_cancelled(
                    user_email=customer.email,
                    user_name=customer.name or "",
                    order_id=str(db_order.id),
                    total=float(getattr(db_order, "total_amount", 0) or 0),
                    cancelled_by="you",
                )
        except Exception:
            logger.warning("Cancellation email failed for order %s", db_order.id, exc_info=True)

        if isinstance(db_order.id,      uuid.UUID): db_order.id      = str(db_order.id)
        if isinstance(db_order.user_id, uuid.UUID): db_order.user_id = str(db_order.user_id)
        return {"id": str(db_order.id), "status": db_order.status}

    except HTTPException:
        raise
    except Exception:
        db.rollback()
        logger.error("cancel_user_order failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to cancel order")


@router.get("/orders/{order_id}/items", response_model=List[OrderItemResponse])
async def get_items_for_order(
    order_id: str,
    db: Session = Depends(get_db),
    user = Depends(JWTBearer())
):
    """Get items for a specific order"""
    try:
        items = get_order_items(db, order_id)
        if not items:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
        for item in items:
            if isinstance(item.id,       uuid.UUID): item.id       = str(item.id)
            if isinstance(item.order_id, uuid.UUID): item.order_id = str(item.order_id)
        return items
    except HTTPException:
        raise
    except Exception:
        logger.error("get_items_for_order failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve order items")