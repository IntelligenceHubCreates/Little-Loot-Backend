# app/coupons/services.py
from datetime import datetime, timezone
from app.coupons.models import Coupon


class CouponError(Exception):
    """Raised when a coupon can't be applied. `.message` is safe to show users."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def has_previous_order(db, user_id) -> bool:
    """True if the customer already has an order that wasn't cancelled."""
    from sqlalchemy import func
    from app.orders.models import Order
    return db.query(Order.id).filter(
        Order.user_id == user_id,
        func.lower(func.coalesce(Order.status, '')) != 'cancelled',
    ).first() is not None


def evaluate_coupon(db, code: str, subtotal: float, user_id=None, require_user: bool = False):
    """Validate a coupon against a cart subtotal and compute the discount.
    Returns (coupon, discount_amount). Raises CouponError on any failure.
    Used by BOTH /validate and order creation so preview == charged amount.

    user_id      -- the signed-in customer, when known (needed for first-order coupons)
    require_user -- True when placing an order: a first-order coupon then needs a
                    signed-in customer. A preview without a login is let through."""
    code = (code or "").upper().strip()
    if not code:
        raise CouponError("Coupon code is required")

    coupon = db.query(Coupon).filter(Coupon.code == code).first()
    if not coupon:
        raise CouponError("Invalid coupon code")
    if not coupon.is_active:
        raise CouponError("Coupon is not active")

    # tz-safe expiry compare (your column is timezone=True; parsed dates may be naive —
    # this is the naive/aware trap your Phase 11 note mentions).
    if coupon.expires_at is not None:
        exp = coupon.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            raise CouponError("Coupon has expired")

    # max_uses 0 / NULL = no limit
    if (coupon.max_uses or 0) > 0 and (coupon.used_count or 0) >= coupon.max_uses:
        raise CouponError("Coupon usage limit reached")
    if getattr(coupon, "first_order_only", False):
        if user_id is None:
            if require_user:
                raise CouponError("Please log in to use this first-order coupon")
        elif has_previous_order(db, user_id):
            raise CouponError("This coupon is only for your first order")
    if subtotal < (coupon.min_order or 0):
        raise CouponError(f"Minimum order amount is ₹{(coupon.min_order or 0):.0f}")

    if coupon.discount_type == "percentage":
        discount = round(subtotal * coupon.discount_value / 100, 2)
    else:
        discount = min(coupon.discount_value, subtotal)

    discount = max(0.0, min(float(discount), subtotal))
    return coupon, discount


def redeem_coupon(db, code: str) -> None:
    """Increment used_count by 1. Call EXACTLY ONCE, at the moment an order is
    confirmed/paid. Does not commit — the caller owns the transaction.
    ⚠️ Placement matters — see the note in the order changes below."""
    if not code:
        return
    coupon = db.query(Coupon).filter(Coupon.code == code.upper().strip()).first()
    if coupon:
        coupon.used_count = (coupon.used_count or 0) + 1