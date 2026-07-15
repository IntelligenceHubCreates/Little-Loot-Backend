# app/orders/schemas.py
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class ProductInOrder(BaseModel):
    """Minimal product info embedded in order item response"""
    id:            str
    name:          str
    category:      Optional[str]  = None
    original_price: float         = 0
    # Include product-level color so frontend can fall back to it
    # when order_item.color is NULL (e.g. product has no variants)
    color:         Optional[str]  = None
    color_hex:     Optional[str]  = None
    product_image: Optional[List[dict]] = None

    class Config:
        from_attributes = True


class OrderItemBase(BaseModel):
    product_id: str  = Field(..., max_length=200)
    quantity:   int  = Field(..., ge=1, le=100)
    price:      float = Field(..., ge=0)
    color:      Optional[str]   = None
    color_hex:  Optional[str]   = None
    image:      Optional[str]   = None


class OrderBase(BaseModel):
    shipping_address: str  = Field(..., max_length=500)
    total_amount:     float = Field(..., ge=0)
    status:           str  = "confirmed"
    coupon_code:      Optional[str] = Field(None, max_length=50)
    order_items:      List[OrderItemBase] = Field(default=[], max_length=50)
    gift_message:     Optional[str] = Field(None, max_length=500)


class OrderCreate(OrderBase):
    pass


class OrderItemResponse(OrderItemBase):
    id:         str
    order_id:   str
    product_id: str
    price:      float       # BUG FIX: was `int` — overrode OrderItemBase.price: float
    quantity:   int
    product:    ProductInOrder

    class Config:
        from_attributes = True


class OrderResponse(OrderBase):
    id:          str
    user_id:     str
    order_date:  datetime
    subtotal:        Optional[float] = None          # ← NEW
    discount_amount: Optional[float] = None          # ← NEW
    delivery_fee:    Optional[float] = None          # ← NEW
    order_items: List[OrderItemResponse] = []
    created_at:  datetime
    updated_at:  datetime
    gift_message: Optional[str] = None

    class Config:
        from_attributes = True


class OrderUpdate(BaseModel):
    status:              Optional[str] = None
    shipping_address_id: Optional[str] = None