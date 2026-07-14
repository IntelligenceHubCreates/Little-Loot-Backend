# app/coupons/schemas.py
from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class CouponCreate(BaseModel):
    code:           str
    discount_type:  str          # 'percentage' | 'flat'
    discount_value: float
    min_order:      float  = 0
    max_uses:       int    = 100
    is_active:      bool   = True
    expires_at:     Optional[str] = None   # ISO date string or None


class CouponUpdate(BaseModel):
    code:           Optional[str]   = None
    discount_type:  Optional[str]   = None
    discount_value: Optional[float] = None
    min_order:      Optional[float] = None
    max_uses:       Optional[int]   = None
    is_active:      Optional[bool]  = None
    expires_at:     Optional[str]   = None


class CouponResponse(BaseModel):
    id:             str
    code:           str
    discount_type:  str
    discount_value: float
    min_order:      float
    max_uses:       int
    used_count:     int
    is_active:      bool
    expires_at:     Optional[datetime]
    created_at:     datetime

    class Config:
        from_attributes = True