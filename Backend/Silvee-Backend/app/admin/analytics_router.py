# app/admin/analytics_router.py
"""
Real-data analytics for the Little Loot admin panel.
Every number here is computed from actual DB rows — no fabricated metrics.

Endpoints:
  GET /api/admin/analytics/overview
  GET /api/admin/analytics/revenue?range=30d[&start=&end=]
  GET /api/admin/analytics/products?range=30d
  GET /api/admin/analytics/orders?range=30d
  GET /api/admin/analytics/customers?range=30d

Mount in main.py:
    from app.admin.analytics_router import analytics_router
    server.include_router(analytics_router)
"""
from __future__ import annotations

import datetime
from datetime import timezone 
from typing import Any, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, cast, Date
from sqlalchemy.orm import Session

from app.db import get_db
from app.users.utils import JWTBearer
from app.users.models import Users, UserAddress
from app.orders.models import Order, OrderItem
from app.products.models import Product

try:
    from app.payments.routers import PaymentOrder
    HAS_PAYMENTS = True
except Exception:
    try:
        from app.payments.router import PaymentOrder  # fallback module name
        HAS_PAYMENTS = True
    except Exception:
        HAS_PAYMENTS = False

analytics_router = APIRouter(prefix="/api/admin/analytics", tags=["Analytics"])


# ── Helpers ───────────────────────────────────────────────────────

def _require_admin(user: dict | None) -> None:
    if user is None or user.get("role") != 1:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorised")

def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(timezone.utc)

def _pct_change(curr: float, prev: float) -> float:
    if prev == 0:
        return 100.0 if curr > 0 else 0.0
    return round((curr - prev) / prev * 100, 1)


def _range_to_window(range_key: str, start: Optional[str], end: Optional[str]) -> Tuple[datetime.datetime, datetime.datetime, str]:
    """
    Returns (start_dt, end_dt, bucket) where bucket ∈ {'day','week','month'}.
    Custom range via start/end (ISO dates) when range_key == 'custom'.
    """
    now = _utcnow()
    end_dt = now

    if range_key == "custom" and start and end:
        try:
            start_dt = datetime.datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
            end_dt = datetime.datetime.fromisoformat(end) + datetime.timedelta(days=1).replace(tzinfo=timezone.utc)  # inclusive
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid custom start/end (use YYYY-MM-DD)")
        span_days = (end_dt - start_dt).days
        bucket = "day" if span_days <= 31 else ("week" if span_days <= 120 else "month")
        return start_dt, end_dt, bucket

    mapping = {
        "today": (1, "day"),
        "7d":    (7, "day"),
        "30d":   (30, "day"),
        "6m":    (180, "week"),
        "1y":    (365, "month"),
    }
    days, bucket = mapping.get(range_key, (30, "day"))
    start_dt = (now - datetime.timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    if range_key == "today":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_dt, end_dt, bucket


def _prev_window(start_dt: datetime.datetime, end_dt: datetime.datetime) -> Tuple[datetime.datetime, datetime.datetime]:
    span = end_dt - start_dt
    return start_dt - span, start_dt


def _bucket_label(dt: datetime.datetime, bucket: str) -> str:
    if bucket == "day":
        return dt.strftime("%d %b")
    if bucket == "week":
        return dt.strftime("%d %b")
    return dt.strftime("%b %Y")


def _iter_buckets(start_dt: datetime.datetime, end_dt: datetime.datetime, bucket: str):
    """Yield (bucket_start, bucket_end, label) covering [start, end)."""
    cur = start_dt
    if bucket == "day":
        cur = cur.replace(hour=0, minute=0, second=0, microsecond=0)
        while cur < end_dt:
            nxt = cur + datetime.timedelta(days=1)
            yield cur, nxt, _bucket_label(cur, bucket)
            cur = nxt
    elif bucket == "week":
        cur = cur.replace(hour=0, minute=0, second=0, microsecond=0)
        while cur < end_dt:
            nxt = cur + datetime.timedelta(days=7)
            yield cur, nxt, _bucket_label(cur, bucket)
            cur = nxt
    else:  # month
        cur = cur.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while cur < end_dt:
            nxt = (cur + datetime.timedelta(days=32)).replace(day=1)
            yield cur, nxt, _bucket_label(cur, bucket)
            cur = nxt


def _revenue_orders_in(session: Session, start_dt, end_dt) -> Tuple[float, int]:
    row = session.query(
        func.coalesce(func.sum(Order.total_amount), 0),
        func.count(Order.id),
    ).filter(Order.created_at >= start_dt, Order.created_at < end_dt).first()
    return float(row[0] or 0), int(row[1] or 0)


# ═══════════════════════════════════════════════════════════════════
# OVERVIEW
# ═══════════════════════════════════════════════════════════════════

@analytics_router.get("/overview")
async def analytics_overview(
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    now = _utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month_start = (month_start - datetime.timedelta(days=1)).replace(day=1)

    # All-time revenue + orders
    total_rev, total_orders = _revenue_orders_in(session, datetime.datetime.min, now + datetime.timedelta(days=1))

    # This month vs last month
    rev_month, orders_month = _revenue_orders_in(session, month_start, now + datetime.timedelta(days=1))
    rev_prev, orders_prev = _revenue_orders_in(session, prev_month_start, month_start)

    aov = round(total_rev / total_orders, 2) if total_orders else 0.0

    # Customers (role != 1 = non-admin)
    total_customers = session.query(func.count(Users.id)).filter(Users.role != 1).scalar() or 0
    new_this_month = session.query(func.count(Users.id)).filter(
        Users.role != 1, Users.created_at >= month_start
    ).scalar() or 0

    # Returning customers = customers with >1 order
    repeat_rows = (
        session.query(Order.user_id)
        .group_by(Order.user_id)
        .having(func.count(Order.id) > 1)
        .all()
    )
    returning_customers = len(repeat_rows)

    # Cancelled rate (case-insensitive status)
    cancelled = session.query(func.count(Order.id)).filter(
        func.lower(Order.status) == "cancelled"
    ).scalar() or 0
    cancelled_rate = round((cancelled / total_orders * 100), 1) if total_orders else 0.0

    # Best-selling product (by units, all-time)
    best = (
        session.query(
            Product.id, Product.name,
            func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
        )
        .join(OrderItem, OrderItem.product_id == Product.id)
        .group_by(Product.id, Product.name)
        .order_by(func.coalesce(func.sum(OrderItem.quantity), 0).desc())
        .first()
    )
    best_seller = {"id": str(best[0]), "name": best[1], "units": int(best[2])} if best else None

    # Low stock count (<=10, active)
    low_stock_count = session.query(func.count(Product.id)).filter(
        Product.is_active.is_(True), Product.count <= 10
    ).scalar() or 0
    out_of_stock_count = session.query(func.count(Product.id)).filter(
        Product.is_active.is_(True), Product.count <= 0
    ).scalar() or 0

    return {
        "total_revenue":        total_rev,
        "revenue_this_month":   rev_month,
        "revenue_trend":        _pct_change(rev_month, rev_prev),
        "total_orders":         total_orders,
        "orders_this_month":    orders_month,
        "orders_trend":         _pct_change(orders_month, orders_prev),
        "avg_order_value":      aov,
        "total_customers":      int(total_customers),
        "new_customers_month":  int(new_this_month),
        "returning_customers":  int(returning_customers),
        "cancelled_rate":       cancelled_rate,
        "best_seller":          best_seller,
        "low_stock_count":      int(low_stock_count),
        "out_of_stock_count":   int(out_of_stock_count),
    }


# ═══════════════════════════════════════════════════════════════════
# REVENUE
# ═══════════════════════════════════════════════════════════════════

@analytics_router.get("/revenue")
async def analytics_revenue(
    range: str = Query("30d", description="today|7d|30d|6m|1y|custom"),
    start: Optional[str] = Query(None, description="YYYY-MM-DD (custom)"),
    end:   Optional[str] = Query(None, description="YYYY-MM-DD (custom)"),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    start_dt, end_dt, bucket = _range_to_window(range, start, end)

    # Pull all orders in window once, bucket in Python (avoids dialect-specific date_trunc)
    orders = (
        session.query(Order.created_at, Order.total_amount)
        .filter(Order.created_at >= start_dt, Order.created_at < end_dt)
        .all()
    )

    series = []
    for b_start, b_end, label in _iter_buckets(start_dt, end_dt, bucket):
        rev = 0.0
        cnt = 0
        for created, amount in orders:
            if b_start <= created < b_end:
                rev += float(amount or 0)
                cnt += 1
        series.append({
            "label":   label,
            "revenue": round(rev, 2),
            "orders":  cnt,
            "aov":     round(rev / cnt, 2) if cnt else 0.0,
        })

    cur_rev = sum(s["revenue"] for s in series)
    cur_orders = sum(s["orders"] for s in series)

    prev_start, prev_end = _prev_window(start_dt, end_dt)
    prev_rev, prev_orders = _revenue_orders_in(session, prev_start, prev_end)

    return {
        "range":          range,
        "bucket":         bucket,
        "series":         series,
        "total_revenue":  round(cur_rev, 2),
        "total_orders":   cur_orders,
        "avg_order_value": round(cur_rev / cur_orders, 2) if cur_orders else 0.0,
        "revenue_trend":  _pct_change(cur_rev, prev_rev),
        "orders_trend":   _pct_change(cur_orders, prev_orders),
        "prev_revenue":   round(prev_rev, 2),
        "prev_orders":    prev_orders,
    }


# ═══════════════════════════════════════════════════════════════════
# PRODUCTS
# ═══════════════════════════════════════════════════════════════════

@analytics_router.get("/products")
async def analytics_products(
    range: str = Query("30d"),
    start: Optional[str] = Query(None),
    end:   Optional[str] = Query(None),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    start_dt, end_dt, _ = _range_to_window(range, start, end)

    # Top sellers in window (units + revenue)
    top_rows = (
        session.query(
            Product.id, Product.name, Product.category,
            func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
            func.coalesce(func.sum(OrderItem.price * OrderItem.quantity), 0).label("revenue"),
        )
        .join(OrderItem, OrderItem.product_id == Product.id)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.created_at >= start_dt, Order.created_at < end_dt)
        .group_by(Product.id, Product.name, Product.category)
        .order_by(func.coalesce(func.sum(OrderItem.quantity), 0).desc())
        .limit(10)
        .all()
    )
    top_products = [
        {"id": str(r[0]), "name": r[1], "category": r[2] or "", "units": int(r[3]), "revenue": float(r[4])}
        for r in top_rows
    ]

    # Slow-moving: active products with the FEWEST units in window (incl. zero)
    sold_subq = (
        session.query(
            OrderItem.product_id.label("pid"),
            func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.created_at >= start_dt, Order.created_at < end_dt)
        .group_by(OrderItem.product_id)
        .subquery()
    )
    slow_rows = (
        session.query(
            Product.id, Product.name, Product.category,
            func.coalesce(sold_subq.c.units, 0).label("units"),
        )
        .outerjoin(sold_subq, sold_subq.c.pid == Product.id)
        .filter(Product.is_active.is_(True))
        .order_by(func.coalesce(sold_subq.c.units, 0).asc(), Product.name.asc())
        .limit(10)
        .all()
    )
    slow_products = [
        {"id": str(r[0]), "name": r[1], "category": r[2] or "", "units": int(r[3])}
        for r in slow_rows
    ]

    # Low stock + out of stock (current, not range-bound)
    low_rows = (
        session.query(Product.id, Product.name, Product.count, Product.category)
        .filter(Product.is_active.is_(True), Product.count > 0, Product.count <= 10)
        .order_by(Product.count.asc())
        .limit(20)
        .all()
    )
    low_stock = [{"id": str(r[0]), "name": r[1], "count": int(r[2]), "category": r[3] or ""} for r in low_rows]

    out_rows = (
        session.query(Product.id, Product.name, Product.category)
        .filter(Product.is_active.is_(True), Product.count <= 0)
        .order_by(Product.name.asc())
        .limit(20)
        .all()
    )
    out_of_stock = [{"id": str(r[0]), "name": r[1], "category": r[2] or ""} for r in out_rows]

    # Category-wise sales (units + revenue) in window
    cat_rows = (
        session.query(
            Product.category,
            func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
            func.coalesce(func.sum(OrderItem.price * OrderItem.quantity), 0).label("revenue"),
        )
        .join(OrderItem, OrderItem.product_id == Product.id)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.created_at >= start_dt, Order.created_at < end_dt)
        .group_by(Product.category)
        .order_by(func.coalesce(func.sum(OrderItem.price * OrderItem.quantity), 0).desc())
        .all()
    )
    category_sales = [
        {"category": r[0] or "Uncategorized", "units": int(r[1]), "revenue": float(r[2])}
        for r in cat_rows
    ]

    return {
        "range":          range,
        "top_products":   top_products,
        "slow_products":  slow_products,
        "low_stock":      low_stock,
        "out_of_stock":   out_of_stock,
        "category_sales": category_sales,
    }


# ═══════════════════════════════════════════════════════════════════
# ORDERS
# ═══════════════════════════════════════════════════════════════════

@analytics_router.get("/orders")
async def analytics_orders(
    range: str = Query("30d"),
    start: Optional[str] = Query(None),
    end:   Optional[str] = Query(None),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    start_dt, end_dt, bucket = _range_to_window(range, start, end)

    # Status breakdown (case-insensitive, all-time — operational view)
    status_rows = (
        session.query(func.lower(Order.status), func.count(Order.id))
        .group_by(func.lower(Order.status))
        .all()
    )
    status_counts: dict[str, int] = {}
    for s, c in status_rows:
        status_counts[(s or "unknown")] = int(c)

    # Normalize common buckets so the frontend always has these keys
    normalized = {
        "pending":    status_counts.get("pending", 0),
        "processing": status_counts.get("processing", 0),
        "confirmed":  status_counts.get("confirmed", 0),
        "shipped":    status_counts.get("shipped", 0),
        "delivered":  status_counts.get("delivered", 0),
        "cancelled":  status_counts.get("cancelled", 0),
    }
    other = {k: v for k, v in status_counts.items() if k not in normalized}

    # Order trend in window
    orders = (
        session.query(Order.created_at)
        .filter(Order.created_at >= start_dt, Order.created_at < end_dt)
        .all()
    )
    trend = []
    for b_start, b_end, label in _iter_buckets(start_dt, end_dt, bucket):
        cnt = sum(1 for (created,) in orders if b_start <= created < b_end)
        trend.append({"label": label, "orders": cnt})

    # Payment-status breakdown via payment_orders (Decision 3)
    payment_breakdown = []
    if HAS_PAYMENTS:
        pay_rows = (
            session.query(func.lower(PaymentOrder.status), func.count(PaymentOrder.id))
            .group_by(func.lower(PaymentOrder.status))
            .all()
        )
        payment_breakdown = [{"status": (s or "unknown"), "count": int(c)} for s, c in pay_rows]

    return {
        "range":             range,
        "status_counts":     normalized,
        "other_statuses":    other,
        "trend":             trend,
        "payment_breakdown": payment_breakdown,
        "payment_available": HAS_PAYMENTS,
    }


# ═══════════════════════════════════════════════════════════════════
# CUSTOMERS
# ═══════════════════════════════════════════════════════════════════

@analytics_router.get("/customers")
async def analytics_customers(
    range: str = Query("30d"),
    start: Optional[str] = Query(None),
    end:   Optional[str] = Query(None),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    start_dt, end_dt, bucket = _range_to_window(range, start, end)

    # New customers in window
    new_in_window = session.query(func.count(Users.id)).filter(
        Users.role != 1, Users.created_at >= start_dt, Users.created_at < end_dt
    ).scalar() or 0

    # New-customer trend
    new_users = (
        session.query(Users.created_at)
        .filter(Users.role != 1, Users.created_at >= start_dt, Users.created_at < end_dt)
        .all()
    )
    new_trend = []
    for b_start, b_end, label in _iter_buckets(start_dt, end_dt, bucket):
        cnt = sum(1 for (created,) in new_users if created and b_start <= created < b_end)
        new_trend.append({"label": label, "customers": cnt})

    # Order frequency distribution (all-time): how many customers have 1, 2, 3+ orders
    freq_rows = (
        session.query(Order.user_id, func.count(Order.id).label("c"))
        .group_by(Order.user_id)
        .all()
    )
    one = sum(1 for _, c in freq_rows if c == 1)
    two = sum(1 for _, c in freq_rows if c == 2)
    three_plus = sum(1 for _, c in freq_rows if c >= 3)
    repeat = two + three_plus

    # Top customers by spend (all-time)
    top_rows = (
        session.query(
            Users.id, Users.name, Users.email,
            func.coalesce(func.sum(Order.total_amount), 0).label("spent"),
            func.count(Order.id).label("orders"),
        )
        .join(Order, Order.user_id == Users.id)
        .filter(Users.role != 1)
        .group_by(Users.id, Users.name, Users.email)
        .order_by(func.coalesce(func.sum(Order.total_amount), 0).desc())
        .limit(10)
        .all()
    )
    top_customers = [
        {"id": str(r[0]), "name": r[1] or r[2], "email": r[2], "spent": float(r[3]), "orders": int(r[4])}
        for r in top_rows
    ]

    # City breakdown via UserAddress (partial coverage — only customers with an address)
    city_rows = (
        session.query(UserAddress.city, func.count(func.distinct(UserAddress.user_id)))
        .group_by(UserAddress.city)
        .order_by(func.count(func.distinct(UserAddress.user_id)).desc())
        .limit(10)
        .all()
    )
    city_breakdown = [{"city": (c or "Unknown"), "customers": int(n)} for c, n in city_rows]
    customers_with_address = session.query(func.count(func.distinct(UserAddress.user_id))).scalar() or 0

    return {
        "range":              range,
        "new_in_window":      int(new_in_window),
        "new_trend":          new_trend,
        "order_frequency":    {"one": one, "two": two, "three_plus": three_plus, "repeat": repeat},
        "top_customers":      top_customers,
        "city_breakdown":     city_breakdown,
        "city_coverage":      int(customers_with_address),
    }