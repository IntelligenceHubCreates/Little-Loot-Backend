# app/admin/routers.py
"""
Admin-only API endpoints for the Little Loot admin panel.
All routes require JWT with role=1 (admin).

Mount in main.py:
    from app.admin.routers import admin_router, category_write_router
    app.include_router(admin_router)
    app.include_router(category_write_router)
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db import get_db
from app.users.utils import JWTBearer

# ── Models ────────────────────────────────────────────────────────
# Import with graceful fallback so the file never crashes on partial setups.

from app.users.models import Users  # always present

try:
    from app.orders.models import Order, OrderItem
    HAS_ORDERS = True
except ImportError:
    HAS_ORDERS = False

try:
    from app.coupons.models import Coupon
    HAS_COUPONS = True
except ImportError:
    HAS_COUPONS = False

try:
    from app.reviews.models import Review
    HAS_REVIEWS = True
except ImportError:
    HAS_REVIEWS = False

try:
    from app.blog.models import BlogPost
    HAS_BLOG = True
except ImportError:
    HAS_BLOG = False

from app.store_settings.models import StoreSetting

from app.products.models import Category, Product

admin_router = APIRouter(prefix="/api/admin", tags=["Admin"])


# ── Auth helper ───────────────────────────────────────────────────

def _require_admin(user: dict | None) -> None:
    if user is None or user.get("role") != 1:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorised")


def _pct_change(curr: float, prev: float) -> float:
    if prev == 0:
        return 100.0 if curr > 0 else 0.0
    return round((curr - prev) / prev * 100, 1)


# ═══════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════

@admin_router.get("/dashboard")
async def get_dashboard(
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)

    now         = datetime.datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_start  = (month_start - datetime.timedelta(days=1)).replace(day=1)

    revenue_this_month = 0.0
    revenue_prev_month = 0.0
    orders_this_month  = 0
    orders_prev_month  = 0
    recent_orders: list[dict] = []
    # All six canonical statuses (was 4 → confirmed & shipped were invisible in
    # the donut AND missing from the cancellation-rate denominator).
    order_status_counts = {
        "pending": 0, "confirmed": 0, "processing": 0,
        "shipped": 0, "delivered": 0, "cancelled": 0,
    }

    if HAS_ORDERS:
        # Orders count = every order placed this month (incl. cancelled).
        curr_orders = session.query(Order).filter(Order.created_at >= month_start).all()
        prev_orders = session.query(Order).filter(
            Order.created_at >= prev_start, Order.created_at < month_start
        ).all()
        orders_this_month = len(curr_orders)
        orders_prev_month = len(prev_orders)

        # Revenue = REALISED revenue → excludes cancelled (both months, so the
        # trend stays apples-to-apples). Drop the status check for gross revenue.
        def _rev(orders):
            return float(sum(
                (getattr(o, "total_amount", 0) or 0)
                for o in orders
                if (o.status or "").lower() != "cancelled"
            ))
        revenue_this_month = _rev(curr_orders)
        revenue_prev_month = _rev(prev_orders)

        # Status breakdown — aggregated in SQL, case-insensitive (status is
        # stored mixed-case across endpoints), all-time over every order.
        for status_val, cnt in (
            session.query(func.lower(Order.status), func.count(Order.id))
            .group_by(func.lower(Order.status))
            .all()
        ):
            key = (status_val or "").strip()
            if key in order_status_counts:
                order_status_counts[key] += int(cnt)

        # Recent orders — eager-load user + items so the serializer emits REAL
        # customer names and item counts (no N+1, no '—', no 0-items).
        recent_raw = (
            session.query(Order)
            .options(
                joinedload(Order.user),
                selectinload(Order.order_items).joinedload(OrderItem.product),
            )
            .order_by(Order.created_at.desc())
            .limit(10)
            .all()
        )
        for o in recent_raw:
            recent_orders.append(_serialize_order(o))
    # ── Customers ─────────────────────────────────────────────────
    total_customers = session.query(func.count(Users.id)).scalar() or 0
    prev_customers  = (
        session.query(func.count(Users.id))
        .filter(Users.created_at < month_start)
        .scalar() or 0
    )

    # ── Top products ──────────────────────────────────────────────
    top_products: list[dict] = []
    if HAS_ORDERS:
        try:
            rows = session.execute(text("""
                SELECT oi.product_id, p.name, p.category, p.original_price,
                       SUM(oi.quantity) AS total_sold
                FROM order_items oi
                JOIN products p ON p.id = oi.product_id
                WHERE p.is_active = TRUE
                GROUP BY oi.product_id, p.name, p.category, p.original_price
                ORDER BY total_sold DESC
                LIMIT 5
            """)).fetchall()
            for r in rows:
                top_products.append({
                    "id":       str(r[0]),
                    "name":     r[1],
                    "category": r[2] or "",
                    "price":    float(r[3] or 0),
                    "sold":     int(r[4]),
                })
        except Exception:
            pass

    if not top_products:
        feat = (
            session.query(Product)
            .filter(Product.is_featured.is_(True), Product.is_active.is_(True))
            .limit(5)
            .all()
        )
        top_products = [
            {"id": str(p.id), "name": p.name, "category": p.category or "", "price": float(p.original_price or 0), "sold": 0}
            for p in feat
        ]

    # ── Revenue chart (last 6 months) ─────────────────────────────
    revenue_chart: list[dict] = []
    if HAS_ORDERS:
        for i in range(5, -1, -1):
            m_start = (now - datetime.timedelta(days=30 * i)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            m_end   = (m_start + datetime.timedelta(days=32)).replace(day=1)
            m_orders = session.query(Order).filter(Order.created_at >= m_start, Order.created_at < m_end).all()
            revenue_chart.append({
                "label":   m_start.strftime("%b"),
                "revenue": int(sum(float(getattr(o, "total_amount", 0) or 0) for o in m_orders)),
                "orders":  len(m_orders),
            })

    _total_status = sum(order_status_counts.values())
    cancel_rate = round(order_status_counts["cancelled"] / _total_status * 100, 1) if _total_status else 0.0

    return {
        "revenue_this_month":  revenue_this_month,
        "orders_this_month":   orders_this_month,
        "total_customers":     total_customers,
        "return_rate":         cancel_rate,
        "revenue_trend":       _pct_change(revenue_this_month, revenue_prev_month),
        "orders_trend":        _pct_change(orders_this_month,  orders_prev_month),
        "customers_trend":     _pct_change(total_customers,    prev_customers),
        "order_status_counts": order_status_counts,
        "recent_orders":       recent_orders,
        "top_products":        top_products,
        "revenue_chart":       revenue_chart,
    }


# ═══════════════════════════════════════════════════════════════════
# ORDERS
# ═══════════════════════════════════════════════════════════════════

def _parse_city_from_address(addr: str | None) -> str:
    """Best-effort city from the free-text shipping address
    ('<street>, <city>, <state> - <pincode>'). Returns '' if unsure."""
    if not addr:
        return ""
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    return parts[-2][:40] if len(parts) >= 2 else ""


def _serialize_order(o: Any) -> dict:
    """Serialize an Order to a dict using its REAL relationships:
    order_items (not a non-existent `items` attr) and the joined Users row.
    Payment fields are left blank rather than faked as 'UPI'/'paid'."""
    items_raw = getattr(o, "order_items", None) or []
    serialized_items = []
    for i in items_raw:
        prod = getattr(i, "product", None)
        serialized_items.append({
            "product_id": str(getattr(i, "product_id", "") or ""),
            "name":       (getattr(prod, "name", None) if prod else None) or "Item",
            "qty":        int(getattr(i, "quantity", 1) or 1),
            "price":      float(getattr(i, "price", 0) or 0),
        })

    u = getattr(o, "user", None)
    return {
        "id":             str(o.id),
        "order_number":   str(o.id)[:8].upper(),
        "user_id":        str(getattr(o, "user_id", "") or ""),
        "customer_name":  (getattr(u, "name", None)  or "") if u else "",   # real name; "" → frontend shows #ID
        "customer_email": (getattr(u, "email", None) or "") if u else "",
        "customer_phone": (getattr(u, "phone", None) or "") if u else "",
        "city":           _parse_city_from_address(getattr(o, "shipping_address", "")),
        "address":        getattr(o, "shipping_address", "") or "",
        "items":          serialized_items,
        "total_amount":   float(getattr(o, "total_amount", 0) or 0),
        "payment_method": "",   # not faked
        "payment_status": "",   # not faked
        "status":         getattr(o, "status", "pending") or "pending",
        "created_at":     o.created_at.isoformat() if getattr(o, "created_at", None) else "",
        "updated_at":     (getattr(o, "updated_at", None) or o.created_at).isoformat()
                          if getattr(o, "created_at", None) else "",
    }

@admin_router.get("/orders")
async def list_orders(
    skip:   int           = Query(0,   ge=0),
    limit:  int           = Query(15,  ge=1, le=100),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)

    if not HAS_ORDERS:
        return {"data": [], "totalCount": 0, "page": 1, "limit": limit}

    q = session.query(Order)
    if status:
        q = q.filter(Order.status.ilike(status))
    if search:
        q = q.filter(
            Order.customer_name.ilike(f"%{search}%")  |
            Order.customer_email.ilike(f"%{search}%") |
            Order.order_number.ilike(f"%{search}%")
        )

    total  = q.count()
    orders = q.order_by(Order.created_at.desc()).offset(skip).limit(limit).all()

    return {
        "data":       [_serialize_order(o) for o in orders],
        "totalCount": total,
        "page":       (skip // limit) + 1,
        "limit":      limit,
    }


@admin_router.patch("/orders/{order_id}/status")
async def update_order_status(
    order_id: str,
    body: Dict[str, str],
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    if not HAS_ORDERS:
        raise HTTPException(status_code=501, detail="Orders model not found")

    allowed = {"pending", "processing", "shipped", "delivered", "cancelled"}
    new_status = (body.get("status") or "").lower()
    if new_status not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid status. Allowed: {', '.join(allowed)}")

    order = session.query(Order).filter(Order.id == UUID(order_id)).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order.status     = new_status.capitalize()
    order.updated_at = datetime.datetime.utcnow()
    # Mirror the delivered-at stamp used by the live PUT path
    # (orders/services.update_order_status_admin) so delivery time is recorded
    # regardless of which writer runs. Set-if-None preserves the first delivery.
    if new_status == "delivered" and order.delivered_at is None:
        order.delivered_at = datetime.datetime.now(datetime.timezone.utc)
    session.commit()
    session.refresh(order)
    return {"id": str(order.id), "status": order.status}

@admin_router.delete("/orders/{order_id}", status_code=204)
async def delete_order(
    order_id: str,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    if not HAS_ORDERS:
        raise HTTPException(status_code=501, detail="Orders model not found")

    order = session.query(Order).filter(Order.id == UUID(order_id)).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    session.delete(order)
    session.commit()


# ═══════════════════════════════════════════════════════════════════
# CUSTOMERS
# ═══════════════════════════════════════════════════════════════════

@admin_router.get("/customers")
async def list_customers(
    skip:    int           = Query(0,  ge=0),
    limit:   int           = Query(15, ge=1, le=100),
    search:  Optional[str] = Query(None),
    segment: Optional[str] = Query(None),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)

    # ── PostgreSQL-compatible approach ────────────────────────────
    # PostgreSQL requires every selected column to appear in GROUP BY
    # or be wrapped in an aggregate. Joining Users + Orders and doing
    # GROUP BY users.id fails because all other user columns must also
    # be listed in GROUP BY (unlike MySQL which allows this).
    #
    # FIX: Aggregate orders in a dedicated subquery first, then LEFT
    # OUTER JOIN it to Users. The outer query has no GROUP BY at all,
    # so PostgreSQL is satisfied.

    if HAS_ORDERS:
        # Subquery: one row per user_id with order stats
        order_agg = (
            session.query(
                Order.user_id.label("uid"),
                func.count(Order.id).label("orders_count"),
                func.coalesce(func.sum(Order.total_amount), 0).label("total_spent"),
            )
            .group_by(Order.user_id)
            .subquery("order_agg")
        )

        q = (
            session.query(
                Users,
                func.coalesce(order_agg.c.orders_count, 0).label("orders_count"),
                func.coalesce(order_agg.c.total_spent,  0).label("total_spent"),
            )
            .outerjoin(order_agg, Users.id == order_agg.c.uid)
            .filter(Users.role != 1)
        )

        # Segment filter — safe to compare against subquery columns directly
        seg_lower = (segment or "").lower().replace("⭐ ", "").strip()
        if seg_lower == "vip":
            q = q.filter(func.coalesce(order_agg.c.orders_count, 0) >= 10)
        elif seg_lower == "new":
            q = q.filter(func.coalesce(order_agg.c.orders_count, 0) == 0)
        elif seg_lower == "regular":
            q = q.filter(
                func.coalesce(order_agg.c.orders_count, 0) > 0,
                func.coalesce(order_agg.c.orders_count, 0) < 10,
            )

    else:
        from sqlalchemy import literal
        q = (
            session.query(
                Users,
                literal(0).label("orders_count"),
                literal(0).label("total_spent"),
            )
            .filter(Users.role != 1)
        )

    # ── Search ────────────────────────────────────────────────────
    if search:
        term = f"%{search}%"
        q = q.filter(
            Users.name.ilike(term)  |
            Users.email.ilike(term) |
            Users.phone.ilike(term)
        )

    total = q.count()
    rows  = q.order_by(Users.created_at.desc()).offset(skip).limit(limit).all()

    def serialize(row: tuple) -> dict:
        u, orders_count, total_spent = row
        return {
            "id":           str(u.id),
            "name":         u.name  or "",
            "email":        u.email or "",
            "phone":        u.phone or "",
            "city":         getattr(u, "city", "") or "",
            "total_orders": int(orders_count),
            "total_spent":  float(total_spent),
            "created_at":   u.created_at.isoformat() if u.created_at else "",
            "is_active":    getattr(u, "is_active", True),
        }

    return {
        "data":       [serialize(r) for r in rows],
        "totalCount": total,
        "page":       (skip // limit) + 1,
        "limit":      limit,
    }


@admin_router.delete("/customers/{customer_id}", status_code=204)
async def delete_customer(
    customer_id: str,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)

    customer = session.query(Users).filter(Users.id == UUID(customer_id)).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if customer.role == 1:
        raise HTTPException(status_code=403, detail="Cannot delete an admin account")
    session.delete(customer)
    session.commit()


# ═══════════════════════════════════════════════════════════════════
# COUPONS
# ═══════════════════════════════════════════════════════════════════

class CouponCreate(BaseModel):
    code:           str
    discount_type:  str   # "percentage" | "flat"
    discount_value: float
    min_order:      float = 0
    max_uses:       int   = 100
    is_active:      bool  = True
    expires_at:     Optional[str] = None


@admin_router.get("/coupons")
async def list_coupons(
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    if not HAS_COUPONS:
        return []

    coupons = session.query(Coupon).order_by(Coupon.created_at.desc()).all()
    return [_serialize_coupon(c) for c in coupons]


def _serialize_coupon(c: Any) -> dict:
    return {
        "id":             str(c.id),
        "code":           c.code,
        "discount_type":  c.discount_type,
        "discount_value": float(c.discount_value),
        "min_order":      float(getattr(c, "min_order",  0) or 0),
        "max_uses":       int(getattr(c,   "max_uses",   100) or 100),
        "used_count":     int(getattr(c,   "used_count", 0) or 0),
        "is_active":      bool(c.is_active),
        "expires_at":     c.expires_at.isoformat() if getattr(c, "expires_at", None) else None,
        "created_at":     c.created_at.isoformat() if getattr(c, "created_at", None) else "",
    }


@admin_router.post("/coupons", status_code=201)
async def create_coupon(
    data: CouponCreate,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    if not HAS_COUPONS:
        raise HTTPException(status_code=501, detail="Coupons model not found")

    # Duplicate code check
    existing = session.query(Coupon).filter(Coupon.code == data.code.upper()).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Coupon code '{data.code.upper()}' already exists")

    if data.discount_type not in ("percentage", "flat"):
        raise HTTPException(status_code=400, detail="discount_type must be 'percentage' or 'flat'")
    if data.discount_type == "percentage" and data.discount_value > 100:
        raise HTTPException(status_code=400, detail="Percentage discount cannot exceed 100")

    expires = None
    if data.expires_at:
        try:
            expires = datetime.datetime.fromisoformat(data.expires_at)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid expires_at format. Use ISO 8601.")

    coupon = Coupon(
        code=data.code.upper(),
        discount_type=data.discount_type,
        discount_value=data.discount_value,
        min_order=data.min_order,
        max_uses=data.max_uses,
        is_active=data.is_active,
        expires_at=expires,
    )
    session.add(coupon)
    session.commit()
    session.refresh(coupon)
    return _serialize_coupon(coupon)


@admin_router.put("/coupons/{coupon_id}")
async def update_coupon(
    coupon_id: str,
    data: Dict[str, Any],
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    if not HAS_COUPONS:
        raise HTTPException(status_code=501, detail="Coupons model not found")

    coupon = session.query(Coupon).filter(Coupon.id == UUID(coupon_id)).first()
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")

    # Coerce types before setting
    if "code" in data:
        data["code"] = str(data["code"]).upper()
    if "expires_at" in data and data["expires_at"]:
        try:
            data["expires_at"] = datetime.datetime.fromisoformat(data["expires_at"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid expires_at format")
    elif "expires_at" in data and not data["expires_at"]:
        data["expires_at"] = None

    allowed_fields = {"code", "discount_type", "discount_value", "min_order", "max_uses", "is_active", "expires_at"}
    for k, v in data.items():
        if k in allowed_fields and hasattr(coupon, k):
            setattr(coupon, k, v)

    session.commit()
    session.refresh(coupon)
    return _serialize_coupon(coupon)


@admin_router.delete("/coupons/{coupon_id}", status_code=204)
async def delete_coupon(
    coupon_id: str,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    if not HAS_COUPONS:
        raise HTTPException(status_code=501, detail="Coupons model not found")

    coupon = session.query(Coupon).filter(Coupon.id == UUID(coupon_id)).first()
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")
    session.delete(coupon)
    session.commit()


# ═══════════════════════════════════════════════════════════════════
# REVIEWS  (reads the real `ratings` table via the Rating model)
# ═══════════════════════════════════════════════════════════════════
# NOTE: reviews live in app/rating/models.py (Rating), NOT app/reviews.
# The `ratings` table has BOTH `review` and `comment` columns (two
# storefront write-paths historically), so we read COALESCE(review, comment).

from app.rating.models import Rating as _Rating

@admin_router.get("/reviews")
async def list_reviews(
    skip:       int            = Query(0,  ge=0),
    limit:      int            = Query(20, ge=1, le=100),
    approved:   Optional[bool] = Query(None, description="Filter by approval status"),
    min_rating: Optional[int]  = Query(None, ge=1, le=5, description="Minimum star rating"),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)

    q = session.query(_Rating)
    if approved is not None:
        q = q.filter(_Rating.is_approved.is_(approved))
    if min_rating is not None:
        q = q.filter(_Rating.rating >= min_rating)

    total   = q.count()
    reviews = q.order_by(_Rating.created_at.desc()).offset(skip).limit(limit).all()

    # Batch-resolve product + customer names (avoid N+1)
    product_ids = {r.product_id for r in reviews if r.product_id}
    user_ids    = {r.user_id    for r in reviews if r.user_id}

    products = {}
    if product_ids:
        for p in session.query(Product).filter(Product.id.in_(product_ids)).all():
            products[p.id] = p.name
    users = {}
    if user_ids:
        for u in session.query(Users).filter(Users.id.in_(user_ids)).all():
            users[u.id] = u.name or u.email

    def serialize_review(r) -> dict:
        # COALESCE(review, comment) — show whichever column holds the text
        text_val = (getattr(r, "review", None) or getattr(r, "comment", None) or "")
        return {
            "id":            str(r.id),
            "product_id":    str(r.product_id) if r.product_id else "",
            "product_name":  products.get(r.product_id, ""),
            "user_id":       str(r.user_id) if r.user_id else "",
            "customer_name": users.get(r.user_id, ""),
            "rating":        int(r.rating),
            "comment":       text_val,
            "is_approved":   bool(getattr(r, "is_approved", True)),
            "created_at":    r.created_at.isoformat() if r.created_at else "",
        }

    return {
        "data":       [serialize_review(r) for r in reviews],
        "totalCount": total,
    }


@admin_router.patch("/reviews/{review_id}/approve")
async def approve_review(
    review_id: str,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    try:
        rid = UUID(review_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid review ID")

    review = session.query(_Rating).filter(_Rating.id == rid).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    review.is_approved = True
    session.commit()
    return {"id": str(review.id), "is_approved": True}


@admin_router.patch("/reviews/{review_id}/reject")
async def reject_review(
    review_id: str,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    """Un-approve a review (hide from storefront) without deleting it."""
    _require_admin(user)
    try:
        rid = UUID(review_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid review ID")

    review = session.query(_Rating).filter(_Rating.id == rid).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    review.is_approved = False
    session.commit()
    return {"id": str(review.id), "is_approved": False}


@admin_router.delete("/reviews/{review_id}", status_code=204)
async def delete_review(
    review_id: str,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    try:
        rid = UUID(review_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid review ID")

    review = session.query(_Rating).filter(_Rating.id == rid).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    session.delete(review)
    session.commit()

# ═══════════════════════════════════════════════════════════════════
# ANALYTICS
# ═══════════════════════════════════════════════════════════════════

@admin_router.get("/analytics")
async def get_analytics(
    period: str = Query("30D", regex="^(7D|30D|1Y)$"),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)

    now  = datetime.datetime.utcnow()
    days = {"7D": 7, "30D": 30, "1Y": 365}[period]
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    revenue_this_month = 0.0
    orders_this_month  = 0
    total_customers    = session.query(func.count(Users.id)).scalar() or 0

    if HAS_ORDERS:
        curr_orders = session.query(Order).filter(Order.created_at >= month_start).all()
        orders_this_month  = len(curr_orders)
        revenue_this_month = float(sum(float(getattr(o, "total_amount", 0) or 0) for o in curr_orders))

    chart: list[dict] = []
    if HAS_ORDERS:
        if period == "7D":
            for i in range(6, -1, -1):
                day = now - datetime.timedelta(days=i)
                ds  = day.replace(hour=0, minute=0, second=0, microsecond=0)
                de  = ds + datetime.timedelta(days=1)
                ords = session.query(Order).filter(Order.created_at >= ds, Order.created_at < de).all()
                rev  = int(sum(float(getattr(o, "total_amount", 0) or 0) for o in ords))
                chart.append({"label": day.strftime("%a"), "revenue": rev, "orders": len(ords), "visitors": len(ords) * 12})

        elif period == "1Y":
            for i in range(11, -1, -1):
                m_start = (now - datetime.timedelta(days=30 * i)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                m_end   = (m_start + datetime.timedelta(days=32)).replace(day=1)
                ords    = session.query(Order).filter(Order.created_at >= m_start, Order.created_at < m_end).all()
                rev     = int(sum(float(getattr(o, "total_amount", 0) or 0) for o in ords))
                chart.append({"label": m_start.strftime("%b"), "revenue": rev, "orders": len(ords), "visitors": len(ords) * 12})

        else:  # 30D — weekly buckets
            for i in range(3, -1, -1):
                ws   = now - datetime.timedelta(days=(i + 1) * 7)
                we   = now - datetime.timedelta(days=i * 7)
                ords = session.query(Order).filter(Order.created_at >= ws, Order.created_at < we).all()
                rev  = int(sum(float(getattr(o, "total_amount", 0) or 0) for o in ords))
                chart.append({"label": ws.strftime("W%V"), "revenue": rev, "orders": len(ords), "visitors": len(ords) * 12})

    traffic = [
        {"source": "Google Search",    "visits": 18240, "pct": 37.5},
        {"source": "Direct / Type-in", "visits": 11350, "pct": 23.4},
        {"source": "Instagram",        "visits": 8920,  "pct": 18.4},
        {"source": "Referral Links",   "visits": 5480,  "pct": 11.3},
        {"source": "Email Campaigns",  "visits": 2740,  "pct": 5.6},
        {"source": "Other",            "visits": 1870,  "pct": 3.8},
    ]
    geo = [
        {"city": "Hyderabad",  "visits": 8420},
        {"city": "Bengaluru",  "visits": 6540},
        {"city": "Vijayawada", "visits": 5380},
        {"city": "Chennai",    "visits": 4380},
        {"city": "Mumbai",     "visits": 3690},
        {"city": "Pune",       "visits": 3040},
        {"city": "Guntur",     "visits": 2360},
    ]

    all_orders_count = session.query(func.count(Order.id)).scalar() if HAS_ORDERS else 0
    funnel = [
        {"step": "Store Visits",     "count": (all_orders_count * 30) or 48600},
        {"step": "Product Views",    "count": (all_orders_count * 10) or 15552},
        {"step": "Added to Cart",    "count": (all_orders_count * 6)  or 9148},
        {"step": "Checkout Started", "count": (all_orders_count * 3)  or 4380},
        {"step": "Orders Placed ✅", "count": all_orders_count         or 1664},
    ]

    return {
        "kpis": {
            "revenue_this_month":  revenue_this_month,
            "orders_this_month":   orders_this_month,
            "total_customers":     total_customers,
            "return_rate":         1.8,
            "revenue_trend":       18.4,
            "orders_trend":        12.0,
            "customers_trend":     9.0,
            "order_status_counts": {"delivered": 0, "processing": 0, "pending": 0, "cancelled": 0},
            "recent_orders":       [],
            "top_products":        [],
            "revenue_chart":       [],
        },
        "traffic": traffic,
        "geo":     geo,
        "funnel":  funnel,
        "chart":   chart,
    }


# ═══════════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# SETTINGS  (real persistence — safe keys only, secrets never stored)
# ═══════════════════════════════════════════════════════════════════

SETTINGS_DEFAULTS: Dict[str, Any] = {
    # Store info
    "store_name":              "Little Loot",
    "store_url":               "littleloot.in",
    "contact_email":           "hello@littleloot.in",
    "support_phone":           "",
    "store_address":           "",
    "gst_number":              "",
    "store_logo":              "",
    "currency":                "INR",
    "timezone":                "Asia/Kolkata",
    # Social
    "social_instagram":        "",
    "social_facebook":         "",
    "social_youtube":          "",
    # Shipping
    "free_shipping_threshold": 999,
    "default_shipping_rate":   49,
    "estimated_delivery_days": 5,
    "cod_enabled":             True,
    # Payment toggles (NEVER keys)
    "upi_enabled":             True,
    "card_enabled":            True,
    "netbanking_enabled":      True,
    "wallet_enabled":          True,
    "online_payment_enabled":  True,
    # Order settings
    "order_prefix":            "LL",
    "invoice_prefix":          "INV",
    "low_stock_threshold":     10,
    "return_window_days":      7,    # days after delivery a return may be requested
    # Notifications
    "notify_order_placed":     True,
    "notify_order_shipped":    True,
    "notify_admin_new_order":  True,
    "notify_low_stock":        True,
    "notify_customer_signup":  True,
    "whatsapp_number":         "",
    # Public analytics IDs (client-side anyway — not secrets)
    "ga_id":                   "",
    "fb_pixel":                "",
    # Appearance
    "primary_color":           "#FF6B6B",
    "accent_color":            "#4ECDC4",
    "show_sale_banner":        True,
}

# Only these keys are ever stored or returned. Everything else is ignored.
SAFE_SETTING_KEYS = set(SETTINGS_DEFAULTS.keys())

# Incoming keys matching any of these patterns are LOUDLY rejected (400).
SECRET_KEY_PATTERNS = ("secret", "password", "passwd", "api_key", "_key", "token", "private", "credential")


def _coerce_setting(raw: Optional[str]) -> Any:
    if raw is None:
        return None
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(raw)
    except (ValueError, TypeError):
        try:
            return float(raw)
        except (ValueError, TypeError):
            return raw


def _read_settings(session: Session) -> Dict[str, Any]:
    result = dict(SETTINGS_DEFAULTS)
    for r in session.query(StoreSetting).all():
        if r.key not in SAFE_SETTING_KEYS:
            continue  # never surface non-whitelisted (or legacy secret) keys
        result[r.key] = _coerce_setting(r.value)
    return result


@admin_router.get("/settings")
async def get_settings(
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    return _read_settings(session)


@admin_router.put("/settings")
async def update_settings(
    data: Dict[str, Any],
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)

    rejected: list[str] = []
    for key, value in data.items():
        lk = str(key).lower()
        if any(pat in lk for pat in SECRET_KEY_PATTERNS):
            rejected.append(key)
            continue
        if key not in SAFE_SETTING_KEYS:
            continue  # ignore unknown (harmless) keys
        str_value = (
            str(value).lower() if isinstance(value, bool)
            else ("" if value is None else str(value))
        )
        row = session.query(StoreSetting).filter(StoreSetting.key == key).first()
        if row:
            row.value = str_value
        else:
            session.add(StoreSetting(key=key, value=str_value))

    session.commit()

    if rejected:
        raise HTTPException(
            status_code=400,
            detail=(f"Secret keys cannot be set from the admin UI: {', '.join(rejected)}. "
                    f"Configure these as environment variables on the server."),
        )

    return _read_settings(session)


@admin_router.get("/settings/integrations-status")
async def integrations_status(
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    """Returns ONLY booleans indicating whether each integration is configured
    in the server environment. Never returns the secret values themselves."""
    _require_admin(user)
    from app.settings import settings as env

    def _has(*attrs: str) -> bool:
        return all(bool(getattr(env, a, None)) for a in attrs)

    return {
        "razorpay":   _has("razorpay_key_id", "razorpay_key_secret"),
        "cloudinary": _has("cloudinary_cloud_name", "cloudinary_api_key", "cloudinary_api_secret"),
    }


@admin_router.post("/settings/upload-logo")
async def upload_store_logo(
    file: UploadFile = File(...),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    """Upload a store logo to Cloudinary; stores the URL under 'store_logo'."""
    _require_admin(user)
    import cloudinary
    import cloudinary.uploader
    from app.settings import settings as env

    cloudinary.config(
        cloud_name=env.cloudinary_cloud_name,
        api_key=env.cloudinary_api_key,
        api_secret=env.cloudinary_api_secret,
    )
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Logo exceeds 5 MB")
    import logging as _logging
    _log = _logging.getLogger(__name__)
    try:
        result = cloudinary.uploader.upload(
            contents,
            folder="littleloot/store",
            resource_type="image",
            allowed_formats=["jpg", "jpeg", "png", "webp", "gif"],
        )
    except Exception:
        _log.error("Logo upload to Cloudinary failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Upload failed. Please try again.")

    url = result["secure_url"]
    row = session.query(StoreSetting).filter(StoreSetting.key == "store_logo").first()
    if row:
        row.value = url
    else:
        session.add(StoreSetting(key="store_logo", value=url))
    session.commit()
    return {"url": url}

# ═══════════════════════════════════════════════════════════════════
# CATEGORIES  (read + write, no auth required on read)
# ═══════════════════════════════════════════════════════════════════

category_write_router = APIRouter(prefix="/api/categories", tags=["Categories"])


class CategoryCreate(BaseModel):
    name:        str
    slug:        str
    parent_id:   Optional[str] = None
    emoji:       Optional[str] = None
    description: Optional[str] = None
    sort_order:  int = 0


def _serialize_category(cat: Category, include_children: bool = True) -> dict:
    d: dict = {
        "id":          str(cat.id),
        "name":        cat.name,
        "slug":        cat.slug,
        "parent_id":   str(cat.parent_id) if cat.parent_id else None,
        "emoji":       cat.emoji,
        "description": cat.description,
        "sort_order":  cat.sort_order,
        "is_active":   getattr(cat, "is_active", True),
    }
    if include_children:
        d["children"] = [_serialize_category(c) for c in getattr(cat, "children", [])]
    return d


@category_write_router.get("")
async def get_categories(session: Session = Depends(get_db)):
    """
    Returns a FLAT list of all categories.
    The frontend's buildCategoryTree() will assemble the tree.
    """
    categories = (
        session.query(Category)
        .order_by(Category.sort_order.asc(), Category.name.asc())
        .all()
    )
    return [_serialize_category(c, include_children=False) for c in categories]


@category_write_router.post("", status_code=201)
async def create_category(
    data: CategoryCreate,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)

    slug = data.slug.strip().lower().replace(" ", "-")
    existing = session.query(Category).filter(Category.slug == slug).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Slug '{slug}' already in use")

    parent_uuid = None
    if data.parent_id:
        try:
            parent_uuid = UUID(data.parent_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid parent_id UUID")
        parent = session.query(Category).filter(Category.id == parent_uuid).first()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent category not found")

    cat = Category(
        name=data.name.strip(),
        slug=slug,
        parent_id=parent_uuid,
        emoji=data.emoji or None,
        description=data.description or None,
        sort_order=data.sort_order,
    )
    session.add(cat)
    session.commit()
    session.refresh(cat)
    return _serialize_category(cat, include_children=False)


@category_write_router.put("/{category_id}")
async def update_category(
    category_id: str,
    data: Dict[str, Any],
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)

    try:
        cat_uuid = UUID(category_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid category ID")

    cat = session.query(Category).filter(Category.id == cat_uuid).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")

    # Prevent self-referencing parent
    if "parent_id" in data:
        if data["parent_id"] == category_id:
            raise HTTPException(status_code=400, detail="A category cannot be its own parent")
        if data["parent_id"]:
            try:
                data["parent_id"] = UUID(data["parent_id"])
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid parent_id UUID")
        else:
            data["parent_id"] = None

    if "slug" in data:
        data["slug"] = str(data["slug"]).strip().lower().replace(" ", "-")

    allowed = {"name", "slug", "emoji", "description", "parent_id", "sort_order", "is_active"}
    for k, v in data.items():
        if k in allowed and hasattr(cat, k):
            setattr(cat, k, v)

    session.commit()
    session.refresh(cat)
    return _serialize_category(cat, include_children=False)


@category_write_router.delete("/{category_id}", status_code=204)
async def delete_category(
    category_id: str,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)

    try:
        cat_uuid = UUID(category_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid category ID")

    cat = session.query(Category).filter(Category.id == cat_uuid).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")

    # Safety: check if any products use this category
    if hasattr(Product, "category_id"):
        product_count = session.query(func.count(Product.id)).filter(Product.category_id == cat_uuid).scalar() or 0
        if product_count > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete: {product_count} product(s) are assigned to this category. Reassign them first."
            )

    session.delete(cat)
    session.commit()

    # ═══════════════════════════════════════════════════════════════════
# LOW STOCK  (Phase 3 — item 11)
# ═══════════════════════════════════════════════════════════════════

@admin_router.get("/low-stock")
async def get_low_stock(
    threshold: int = Query(10, ge=0, le=1000),
    limit:     int = Query(20, ge=1, le=100),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    """Active products at or below the stock threshold, lowest first."""
    _require_admin(user)

    rows = (
        session.query(Product)
        .filter(Product.is_active.is_(True), Product.count <= threshold)
        .order_by(Product.count.asc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id":       str(p.id),
            "name":     p.name,
            "count":    int(p.count or 0),
            "category": p.category or None,
        }
        for p in rows
    ]