# app/cart/router.py
# No changes from your original — router.py stays exactly the same.
# The color fields flow through CartItemCreate → services.add_to_cart automatically.
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db import get_db
from app.cart import services
from app.cart.schemas import CartItemCreate, CartItemUpdate, CartResponse
from app.users.utils import JWTBearer

router = APIRouter(prefix="/api/cart", tags=["Cart"])


@router.get("")
async def get_cart(
    user = Depends(JWTBearer()),
    db: Session = Depends(get_db)
):
    user_id = user.get('id')
    cart = services.get_cart(db, user_id)
    if not cart:
        cart = services.get_or_create_cart(db, user_id)
    return cart


@router.post("/items", status_code=status.HTTP_201_CREATED)
async def add_to_cart(
    item: CartItemCreate,
    user = Depends(JWTBearer()),
    db: Session = Depends(get_db)
):
    user_id = user.get('id')
    return services.add_to_cart(db, user_id, item)


@router.put("/items/{item_id}")
async def update_cart_item(
    item_id: str,
    item_update: CartItemUpdate,
    user = Depends(JWTBearer()),
    db: Session = Depends(get_db)
):
    user_id = user.get('id')
    cart_item = services.update_cart_item(db, user_id, item_id, item_update)
    if not cart_item:
        raise HTTPException(status_code=404, detail="Cart item not found")
    return cart_item


@router.delete("/items/{item_id}")
async def remove_from_cart(
    item_id: str,
    user = Depends(JWTBearer()),
    db: Session = Depends(get_db)
):
    user_id = user.get('id')
    if not services.remove_from_cart(db, user_id, item_id):
        raise HTTPException(status_code=404, detail="Cart item not found")
    return {"message": "Item removed from cart"}


@router.delete("")
async def clear_cart(
    user = Depends(JWTBearer()),
    db: Session = Depends(get_db)
):
    user_id = user.get('id')
    if not services.clear_cart(db, user_id):
        raise HTTPException(status_code=404, detail="Cart not found")
    return {"message": "Cart cleared"}