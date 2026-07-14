# app/cart/schemas.py
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from app.products.schemas import ProductBase


class CartItemBase(BaseModel):
    product_id: str
    quantity:   int = Field(..., ge=1)          # ← must be at least 1
    color:      Optional[str] = None
    color_hex:  Optional[str] = None
    image:      Optional[str] = None


class CartItemCreate(CartItemBase):
    pass


class CartItemUpdate(BaseModel):
    quantity: int = Field(..., ge=1)            # ← must be at least 1


class CartItemResponse(CartItemBase):
    id:            str
    cart_id:       str
    product:       Optional[ProductBase] = None
    product_count: Optional[int]         = None

    class Config:
        from_attributes = True


class CartResponse(BaseModel):
    id:         str
    user_id:    str
    created_at: datetime
    updated_at: Optional[datetime] = None
    cart_items: List[CartItemResponse] = []

    class Config:
        from_attributes = True