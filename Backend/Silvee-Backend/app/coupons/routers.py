# app/coupons/router.py
import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.users.utils import JWTBearer
from app.coupons.models import Coupon
from app.coupons.schemas import CouponCreate, CouponUpdate, CouponResponse

coupon_router = APIRouter(prefix="/api/admin/coupons", tags=["Coupons"])


def _serialize(c: Coupon) -> dict:
    return {
        "id":             str(c.id),
        "code":           c.code,
        "discount_type":  c.discount_type,
        "discount_value": c.discount_value,
        "min_order":      c.min_order      or 0,
        "max_uses":       c.max_uses       or 100,
        "used_count":     c.used_count     or 0,
        "is_active":      c.is_active,
        "expires_at":     c.expires_at.isoformat() if c.expires_at else None,
        "created_at":     c.created_at.isoformat() if c.created_at else None,
    }


def _admin(user):
    if not user or user.get("role") != 1:
        raise HTTPException(status_code=401, detail="Unauthorised")


# ── GET all ──────────────────────────────────────────────────────

@coupon_router.get("", response_model=List[dict])
async def list_coupons(
    db: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    _admin(user)
    coupons = db.query(Coupon).order_by(Coupon.created_at.desc()).all()
    return [_serialize(c) for c in coupons]


# ── POST create ───────────────────────────────────────────────────

@coupon_router.post("", response_model=dict, status_code=201)
async def create_coupon(
    body: CouponCreate,
    db: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    _admin(user)

    # Validate
    if body.discount_type not in ("percentage", "flat"):
        raise HTTPException(400, "discount_type must be 'percentage' or 'flat'")
    if body.discount_type == "percentage" and body.discount_value > 100:
        raise HTTPException(400, "Percentage discount cannot exceed 100")
    if body.discount_value <= 0:
        raise HTTPException(400, "discount_value must be positive")

    # Check duplicate code
    existing = db.query(Coupon).filter(Coupon.code == body.code.upper()).first()
    if existing:
        raise HTTPException(400, f"Coupon code '{body.code}' already exists")

    expires = None
    if body.expires_at:
        try:
            expires = datetime.fromisoformat(body.expires_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "Invalid expires_at date format")

    coupon = Coupon(
        code           = body.code.upper().strip(),
        discount_type  = body.discount_type,
        discount_value = body.discount_value,
        min_order      = body.min_order,
        max_uses       = body.max_uses,
        is_active      = body.is_active,
        expires_at     = expires,
    )
    db.add(coupon)
    try:
        db.commit()
        db.refresh(coupon)
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to create coupon")

    return _serialize(coupon)


# ── PUT update ────────────────────────────────────────────────────

@coupon_router.put("/{coupon_id}", response_model=dict)
async def update_coupon(
    coupon_id: str,
    body: CouponUpdate,
    db: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    _admin(user)

    coupon = db.query(Coupon).filter(Coupon.id == coupon_id).first()
    if not coupon:
        raise HTTPException(404, "Coupon not found")

    if body.code is not None:
        code = body.code.upper().strip()
        dup  = db.query(Coupon).filter(Coupon.code == code, Coupon.id != coupon_id).first()
        if dup:
            raise HTTPException(400, f"Code '{code}' already in use")
        coupon.code = code

    if body.discount_type  is not None: coupon.discount_type  = body.discount_type
    if body.discount_value is not None:
        if body.discount_type == "percentage" and body.discount_value > 100:
            raise HTTPException(400, "Percentage cannot exceed 100")
        coupon.discount_value = body.discount_value
    if body.min_order  is not None: coupon.min_order  = body.min_order
    if body.max_uses   is not None: coupon.max_uses   = body.max_uses
    if body.is_active  is not None: coupon.is_active  = body.is_active
    if body.expires_at is not None:
        if body.expires_at == "":
            coupon.expires_at = None
        else:
            try:
                coupon.expires_at = datetime.fromisoformat(body.expires_at.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(400, "Invalid expires_at format")

    try:
        db.commit()
        db.refresh(coupon)
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to update coupon")

    return _serialize(coupon)


# ── DELETE ────────────────────────────────────────────────────────

@coupon_router.delete("/{coupon_id}", status_code=204)
async def delete_coupon(
    coupon_id: str,
    db: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    _admin(user)
    coupon = db.query(Coupon).filter(Coupon.id == coupon_id).first()
    if not coupon:
        raise HTTPException(404, "Coupon not found")
    db.delete(coupon)
    db.commit()


# ── POST validate (used at checkout) ─────────────────────────────

from app.coupons.services import evaluate_coupon, CouponError   # add at top

@coupon_router.post("/validate", response_model=dict)
async def validate_coupon(
    body: dict,
    db: Session = Depends(get_db),
):
    """Public endpoint — called from the cart/checkout to preview a coupon."""
    try:
        coupon, discount = evaluate_coupon(
            db,
            body.get("code") or "",
            float(body.get("order_total") or 0),
        )
    except CouponError as ce:
        raise HTTPException(400, ce.message)

    return {
        "valid":           True,
        "code":            coupon.code,
        "discount_type":   coupon.discount_type,
        "discount_value":  coupon.discount_value,
        "discount_amount": discount,
        "message":         f"{coupon.discount_value:.0f}"
                           f"{'%' if coupon.discount_type == 'percentage' else '₹'} off applied!",
    }

# app/coupons/router.py — add this route (public, no _admin gate)

from datetime import datetime, timezone

@coupon_router.get("/public", response_model=List[dict])
async def list_public_coupons(db: Session = Depends(get_db)):
    """Customer-facing coupon catalog: only active, unexpired, non-exhausted
    coupons. Deliberately omits used_count/max_uses (internal-only)."""
    now = datetime.now(timezone.utc)
    coupons = (
        db.query(Coupon)
        .filter(Coupon.is_active == True)  # noqa: E712
        .order_by(Coupon.created_at.desc())
        .all()
    )
    out = []
    for c in coupons:
        # tz-safe expiry check (your column is tz-aware; guard naive values)
        if c.expires_at is not None:
            exp = c.expires_at if c.expires_at.tzinfo else c.expires_at.replace(tzinfo=timezone.utc)
            if exp < now:
                continue
        # hide fully-exhausted coupons
        if (c.max_uses or 0) and (c.used_count or 0) >= c.max_uses:
            continue
        out.append({
            "id":             str(c.id),
            "code":           c.code,
            "discount_type":  c.discount_type,      # 'percentage' | 'flat'
            "discount_value": c.discount_value,
            "min_order":      c.min_order or 0,
            "expires_at":     c.expires_at.isoformat() if c.expires_at else None,
        })
    return out