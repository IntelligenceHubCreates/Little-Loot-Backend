from pydantic import BaseModel, validator
from typing import Any, List, Optional
from datetime import datetime


class ProductCreate(BaseModel):
    productName:           str
    productCategory:       str
    productDescription:    str
    productPrice:          int
    productCount:          int
    productDiscount:       int
    productDiscountAmount: int
    productImages:         list
    productDetails:        list

    class Config:
        from_attributes = True


class ProductUpdate(BaseModel):
    name:                 Optional[str]      = None
    category:             Optional[str]      = None
    sub_category_slug:    Optional[str]      = None
    sub_category_name:    Optional[str]      = None
    description:          Optional[str]      = None
    details:              Optional[List[Any]] = None
    original_price:       Optional[int]      = None
    amount_discount:      Optional[int]      = None
    percentage_discount:  Optional[int]      = None
    count:                Optional[int]      = None
    product_image:        Optional[List[Any]] = None
    brand:                Optional[str]      = None
    age_range:            Optional[str]      = None
    is_new:               Optional[bool]     = None
    is_featured:          Optional[bool]     = None
    is_active:            Optional[bool]     = None
    offer_expiration_date: Optional[datetime] = None
    variant_group_id:     Optional[str] = None
    color:                Optional[str] = None
    color_hex:            Optional[str] = None

    class Config:
        from_attributes = True


class ProductResponse(BaseModel):
    id:                   str
    name:                 str
    category_id:          Optional[str]
    category:             Optional[str]
    sub_category_slug:    Optional[str]
    sub_category_name:    Optional[str]
    variant_group_id:     Optional[str] = None
    color:                Optional[str] = None
    color_hex:            Optional[str] = None
    description:          Optional[str]
    details:              List[Any]    = []
    original_price:       int
    amount_discount:      int          = 0
    percentage_discount:  int          = 0
    count:                int          = 0
    product_image:        List[Any]    = []
    brand:                Optional[str]
    age_range:            Optional[str]
    is_new:               bool         = False
    is_featured:          bool         = False
    is_active:            bool         = True
    offer_expiration_date: Optional[datetime]
    created_at:           Optional[datetime]

    class Config:
        from_attributes = True

    @validator("id", "category_id", pre=True)
    def uuid_to_str(cls, v):
        return str(v) if v else None


class ProductListResponse(BaseModel):
    data:       List[ProductResponse]
    totalCount: int
    page:       int = 1
    limit:      int = 20

# Alias so cart/schemas.py and any other imports keep working
ProductBase = ProductResponse