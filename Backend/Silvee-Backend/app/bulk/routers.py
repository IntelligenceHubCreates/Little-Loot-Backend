# app/bulk/routers.py
"""
Bulk order enquiries — quote requests for 25+ units of any product.

  POST /api/bulk-enquiries              public (login optional), rate limited
  GET  /api/bulk-enquiries/my           the logged-in customer's enquiries
  GET  /api/admin/bulk-enquiries        admin list (status / search / paging)
  PUT  /api/admin/bulk-enquiries/{id}   admin: status, quoted price, notes

On a new enquiry the store gets an email (reply-to = the customer) and the
customer gets a confirmation if they left an email address.
"""
from __future__ import annotations

import datetime
import re
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session

from app.bulk.models import BULK_MAX_QUANTITY, BULK_MIN_QUANTITY, BULK_STATUSES, BulkEnquiry
from app.db import get_db
from app.limiter import limiter
from app.products.models import Product
from app.users.utils import JWTBearer

bulk_router = APIRouter(prefix="/api/bulk-enquiries", tags=["Bulk orders"])
admin_bulk_router = APIRouter(prefix="/api/admin/bulk-enquiries", tags=["Admin - Bulk orders"])


def _require_admin(user) -> None:
    if not user or user.get("role") != 1:
        raise HTTPException(status_code=401, detail="Unauthorised")


def _phone10(v: str) -> str:
    d = re.sub(r"\D", "", v or "")
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    return d


def _clean(v: Optional[str], n: int) -> Optional[str]:
    v = re.sub(r"\s+", " ", (v or "")).strip()
    return v[:n] or None


# ── Schemas ───────────────────────────────────────────────────────

class BulkEnquiryIn(BaseModel):
    product_id: Optional[str] = None
    product_query: Optional[str] = Field(None, max_length=200)   # free text when no product chosen
    quantity: int = Field(..., ge=BULK_MIN_QUANTITY, le=BULK_MAX_QUANTITY)
    name: str = Field(..., min_length=2, max_length=120)
    phone: str = Field(..., max_length=20)
    email: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=120)
    needed_by: Optional[datetime.date] = None
    personalise: Optional[bool] = None
    notes: Optional[str] = Field(None, max_length=1000)

    @field_validator("phone")
    @classmethod
    def _v_phone(cls, v: str) -> str:
        d = _phone10(v)
        if not re.fullmatch(r"[6-9]\d{9}", d):
            raise ValueError("Please enter a valid 10-digit mobile number")
        return d

    @field_validator("email")
    @classmethod
    def _v_email(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        if not v:
            return None
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("Please enter a valid email address")
        return v.lower()

    @field_validator("needed_by")
    @classmethod
    def _v_date(cls, v: Optional[datetime.date]) -> Optional[datetime.date]:
        if v and v < datetime.date.today():
            raise ValueError("The date can't be in the past")
        return v


class BulkEnquiryUpdate(BaseModel):
    status: Optional[str] = None
    quoted_price: Optional[float] = Field(None, ge=0, le=10_000_000)
    admin_notes: Optional[str] = Field(None, max_length=4000)

    @field_validator("status")
    @classmethod
    def _v_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in BULK_STATUSES:
            raise ValueError(f"status must be one of {BULK_STATUSES}")
        return v


def _serialize(e: BulkEnquiry, admin: bool = False) -> dict:
    out = {
        "id":           str(e.id),
        "reference":    "BQ-" + str(e.id)[:6].upper(),
        "product_id":   str(e.product_id) if e.product_id else None,
        "product_name": e.product_name,
        "product_sku":  e.product_sku,
        "quantity":     int(e.quantity),
        "needed_by":    e.needed_by.isoformat() if e.needed_by else None,
        "personalise":  e.personalise,
        "notes":        e.notes,
        "status":       e.status,
        "quoted_price": float(e.quoted_price) if e.quoted_price is not None else None,
        "created_at":   e.created_at.isoformat() if e.created_at else None,
    }
    if admin:
        out.update({
            "name": e.name, "phone": e.phone, "email": e.email, "city": e.city,
            "admin_notes": e.admin_notes, "user_id": str(e.user_id) if e.user_id else None,
            "updated_at": e.updated_at.isoformat() if e.updated_at else None,
        })
    return out


# ── Public ────────────────────────────────────────────────────────

@bulk_router.post("", status_code=201)
@limiter.limit("5/minute")
def create_bulk_enquiry(
    request: Request,
    body: BulkEnquiryIn,
    session: Session = Depends(get_db),
    user=Depends(JWTBearer(auto_error=False)),
):
    product = None
    if body.product_id:
        try:
            product = session.query(Product).filter(Product.id == UUID(body.product_id)).first()
        except ValueError:
            product = None
        if not product:
            raise HTTPException(status_code=404, detail="That product is no longer available")
    elif not _clean(body.product_query, 200) and not _clean(body.notes, 1000):
        raise HTTPException(status_code=400, detail="Tell us which product you need, or describe it in the notes")

    user_id = None
    if user and user.get("id"):
        try:
            user_id = UUID(str(user["id"]))
        except ValueError:
            user_id = None

    notes = _clean(body.notes, 1000)
    query_text = _clean(body.product_query, 200)
    e = BulkEnquiry(
        product_id=product.id if product else None,
        product_name=product.name if product else query_text,
        product_sku=getattr(product, "sku", None) if product else None,
        user_id=user_id,
        quantity=body.quantity,
        name=_clean(body.name, 120) or "Customer",
        phone=body.phone,
        email=body.email,
        city=_clean(body.city, 120),
        needed_by=body.needed_by,
        personalise=None if body.personalise is None else ("yes" if body.personalise else "no"),
        notes=notes,
        status="new",
    )
    session.add(e)
    session.commit()
    session.refresh(e)

    from app.email.service import send_bulk_enquiry_to_store, send_bulk_enquiry_confirmation
    data = _serialize(e, admin=True)
    send_bulk_enquiry_to_store(data)
    if e.email:
        send_bulk_enquiry_confirmation(data)
    return _serialize(e)


@bulk_router.get("/my")
def my_bulk_enquiries(session: Session = Depends(get_db), user=Depends(JWTBearer())):
    if not user or not user.get("id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    rows = (session.query(BulkEnquiry)
            .filter(BulkEnquiry.user_id == UUID(str(user["id"])))
            .order_by(BulkEnquiry.created_at.desc()).limit(50).all())
    return [_serialize(r) for r in rows]


# ── Admin ─────────────────────────────────────────────────────────

@admin_bulk_router.get("")
def admin_list_bulk_enquiries(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None, max_length=100),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    _require_admin(user)
    q = session.query(BulkEnquiry)
    if status and status in BULK_STATUSES:
        q = q.filter(BulkEnquiry.status == status)
    if search and search.strip():
        term = f"%{search.strip()}%"
        q = q.filter(or_(
            BulkEnquiry.name.ilike(term), BulkEnquiry.phone.ilike(term), BulkEnquiry.email.ilike(term),
            BulkEnquiry.product_name.ilike(term), BulkEnquiry.product_sku.ilike(term),
            BulkEnquiry.city.ilike(term), cast(BulkEnquiry.id, String).ilike(f"{search.strip().lower().removeprefix('bq-')}%"),
        ))
    total = q.count()
    rows = q.order_by(BulkEnquiry.created_at.desc()).offset(skip).limit(limit).all()
    counts = dict(session.query(BulkEnquiry.status, func.count(BulkEnquiry.id)).group_by(BulkEnquiry.status).all())
    return {
        "data": [_serialize(r, admin=True) for r in rows],
        "totalCount": total,
        "counts": {s: int(counts.get(s, 0)) for s in BULK_STATUSES},
    }


@admin_bulk_router.put("/{enquiry_id}")
def admin_update_bulk_enquiry(
    enquiry_id: str,
    body: BulkEnquiryUpdate,
    session: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    _require_admin(user)
    try:
        uid = UUID(enquiry_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid enquiry id")
    e = session.query(BulkEnquiry).filter(BulkEnquiry.id == uid).first()
    if not e:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    fields = body.model_fields_set
    if "status" in fields and body.status:
        e.status = body.status
    if "quoted_price" in fields:
        e.quoted_price = Decimal(str(body.quoted_price)) if body.quoted_price is not None else None
    if "admin_notes" in fields:
        e.admin_notes = body.admin_notes
    session.commit()
    session.refresh(e)
    return _serialize(e, admin=True)
