# app/cart/services.py
from sqlalchemy.orm import Session
from app.cart.models import Cart, CartItem
from app.cart.schemas import CartItemCreate, CartItemUpdate
from app.products.models import Product
from typing import Optional
from fastapi import HTTPException, status
from sqlalchemy import desc


def get_or_create_cart(db: Session, user_id: str) -> Cart:
    cart = db.query(Cart).filter(Cart.user_id == user_id).first()
    if not cart:
        cart = Cart(user_id=user_id)
        db.add(cart)
        db.commit()
        db.refresh(cart)
    return cart


def get_cart(db: Session, user_id: str) -> Optional[Cart]:
    cart = db.query(Cart).filter(Cart.user_id == user_id).first()
    if not cart:
        return None

    cart_items = (
        db.query(CartItem)
        .filter(CartItem.cart_id == cart.id)
        .order_by(desc(CartItem.created_at))
        .all()
    )
    cart.cart_items = cart_items

    for item in cart.cart_items:
        item.product = db.query(Product).filter(Product.id == item.product_id).first()

    return cart


def add_to_cart(db: Session, user_id: str, item: CartItemCreate) -> CartItem:
    cart = get_or_create_cart(db, user_id)

    product = db.query(Product).filter(Product.id == item.product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    # ── NEW: block inactive/unlisted products ─────────────────────
    if not getattr(product, "is_active", True):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This product is currently unavailable",
        )

    if product.count < item.quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only {product.count} items available in stock",
        )

    if item.color:
        existing_item = (
            db.query(CartItem)
            .filter(
                CartItem.cart_id    == cart.id,
                CartItem.product_id == item.product_id,
                CartItem.color      == item.color,
            )
            .first()
        )
    else:
        existing_item = (
            db.query(CartItem)
            .filter(
                CartItem.cart_id    == cart.id,
                CartItem.product_id == item.product_id,
                CartItem.color.is_(None),
            )
            .first()
        )

    if existing_item:
        if product.count < (existing_item.quantity + item.quantity):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Only {product.count - existing_item.quantity} more items available in stock",
            )
        existing_item.quantity += item.quantity
        db.commit()
        db.refresh(existing_item)
        return existing_item

    cart_item = CartItem(
        cart_id    = cart.id,
        product_id = item.product_id,
        quantity   = item.quantity,
        color      = item.color     or None,
        color_hex  = item.color_hex or None,
        image      = item.image     or None,
    )
    db.add(cart_item)
    db.commit()
    db.refresh(cart_item)
    return cart_item


def update_cart_item(
    db: Session, user_id: str, item_id: str, item_update: CartItemUpdate
) -> Optional[CartItem]:
    cart = get_cart(db, user_id)
    if not cart:
        return None

    cart_item = (
        db.query(CartItem)
        .filter(CartItem.id == item_id, CartItem.cart_id == cart.id)
        .first()
    )
    if not cart_item:
        return None

    product = db.query(Product).filter(Product.id == cart_item.product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    # ── NEW: don't allow raising quantity on an inactive product ──
    if not getattr(product, "is_active", True):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This product is currently unavailable",
        )

    if product.count < item_update.quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only {product.count} items available in stock",
        )

    cart_item.quantity = item_update.quantity
    db.commit()
    db.refresh(cart_item)
    return cart_item


def remove_from_cart(db: Session, user_id: str, item_id: str) -> bool:
    cart = get_cart(db, user_id)
    if not cart:
        return False

    cart_item = (
        db.query(CartItem)
        .filter(CartItem.id == item_id, CartItem.cart_id == cart.id)
        .first()
    )
    if not cart_item:
        return False

    db.delete(cart_item)
    db.commit()
    return True


def clear_cart(db: Session, user_id: str) -> bool:
    cart = get_cart(db, user_id)
    if not cart:
        return False

    db.query(CartItem).filter(CartItem.cart_id == cart.id).delete()
    db.commit()
    return True