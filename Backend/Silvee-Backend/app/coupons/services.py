# app/coupons/services.py
from datetime import datetime, timezone
from app.coupons.models import Coupon


class CouponError(Exception):
    """Raised when a coupon can't be applied. `.message` is safe to show users."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def evaluate_coupon(db, code: str, subtotal: float):
    """Validate a coupon against a cart subtotal and compute the discount.
    Returns (coupon, discount_amount). Raises CouponError on any failure.
    Used by BOTH /validate and order creation so preview == charged amount."""
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

    if (coupon.used_count or 0) >= (coupon.max_uses or 0):
        raise CouponError("Coupon usage limit reached")
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