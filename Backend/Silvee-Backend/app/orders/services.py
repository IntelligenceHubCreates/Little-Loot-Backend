# app/orders/services.py
from datetime import datetime, timezone
from typing import List

from sqlalchemy.orm import Session, joinedload

from app.orders.models import Order, OrderItem
from app.orders.schemas import OrderCreate, OrderUpdate
from app.products.models import Product

# B-5: Map admin-set order statuses to notification events the dispatcher understands.
# Keys are lowercase; spaces normalised to underscores before lookup.
_STATUS_NOTIFICATION_EVENT = {
    "packed":            "packed",
    "shipped":           "shipped",
    "out_for_delivery":  "out_for_delivery",
    "delivered":         "delivered",
    "delivery_failed":   "delivery_failed",
}


def _try_emit_status_notification(db: Session, db_order: Order, status: str) -> None:
    """Fire an in-app (and optionally WhatsApp) notification for a status change.
    Swallows all exceptions — notifications must never break a status update."""
    try:
        event = _STATUS_NOTIFICATION_EVENT.get((status or "").lower().replace(" ", "_"))
        if not event or not db_order.user_id:
            return
        from app.notifications.dispatcher import emit_notification
        from app.users.models import Users
        user = db.query(Users).filter(Users.id == db_order.user_id).first()
        # Format Indian mobile number to E.164 for WhatsApp (+91XXXXXXXXXX)
        phone_e164 = None
        if user and user.phone:
            digits = "".join(c for c in user.phone if c.isdigit())
            if len(digits) == 10:
                phone_e164 = f"+91{digits}"
        emit_notification(
            db,
            event=event,
            user_id=db_order.user_id,
            order_id=db_order.id,
            phone_e164=phone_e164,
            courier_name=getattr(db_order, "courier_name", "") or "",
            awb_number=getattr(db_order, "awb_number", "") or "",
        )
    except Exception:
        pass  # never break the caller's flow


def create_order(db: Session, user_id: str, order: dict) -> Order:
    """Create a new order"""
    from app.coupons.services import redeem_coupon   # local import avoids any cycle
    try:
        db_order = Order(
            user_id          = user_id,
            shipping_address = order['shipping_address'],
            total_amount     = order['total_amount'],
            status           = order['status'],
            order_date       = datetime.now(timezone.utc),
            # ── breakdown (NEW) ──
            subtotal         = order.get('subtotal'),
            discount_amount  = order.get('discount_amount') or 0,
            delivery_fee     = order.get('delivery_fee') or 0,
            coupon_code      = order.get('coupon_code'),
            gift_message = (order.get('gift_message') or '').strip()[:500] or None,
        )
        db.add(db_order)
        db.flush()

        for item in order['order_items']:
            db_item = OrderItem(
                order_id   = db_order.id,
                product_id = item['product_id'],
                quantity   = item['quantity'],
                price      = item['price'],
                color      = item.get('color')     or None,
                color_hex  = item.get('color_hex') or None,
                image      = item.get('image')     or None,
            )
            db.add(db_item)

        # ── Redeem the coupon in the SAME transaction ──
        # ⚠️ SEE THE PLACEMENT NOTE BELOW before shipping.
        if order.get('coupon_code'):
            redeem_coupon(db, order['coupon_code'])

        db.commit()
        db.refresh(db_order)
        return db_order
    except Exception as e:
        db.rollback()
        raise e

def get_user_orders(db: Session, user_id: str) -> List[Order]:
    """Get all orders for user"""
    return db.query(Order).filter(Order.user_id == user_id).all()


def get_order(db: Session, user_id: str, order_id: str) -> Order:
    """Get specific order for user"""
    return db.query(Order).filter(
        Order.id      == order_id,
        Order.user_id == user_id
    ).options(
        joinedload(Order.order_items).joinedload(OrderItem.product)
    ).first()


def update_order(
    db: Session,
    user_id: str,
    order_id: str,
    order: OrderUpdate
) -> Order:
    """Update order"""
    try:
        db_order = get_order(db, user_id, order_id)
        if not db_order:
            return None

        for key, value in order.model_dump(exclude_unset=True).items():
            setattr(db_order, key, value)

        db_order.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(db_order)
        return db_order
    except Exception as e:
        db.rollback()
        raise e


def get_order_items(db: Session, order_id: str) -> List[OrderItem]:
    """Get all items for an order"""
    return db.query(OrderItem).filter(OrderItem.order_id == order_id).all()


def get_all_orders_admin(db: Session) -> List[Order]:
    """Get all orders for admin"""
    return db.query(Order).options(
        joinedload(Order.order_items).joinedload(OrderItem.product)
    ).all()


def update_order_status_admin(
    db: Session,
    order_id: str,
    status: str
) -> Order:
    """Update order status (admin). Emits an in-app notification atomically."""
    try:
        db_order = db.query(Order).filter(Order.id == order_id).first()
        if not db_order:
            return None

        db_order.status     = status
        db_order.updated_at = datetime.now(timezone.utc)
        # Stamp delivery time ONCE — the first time this order becomes
        # "delivered". Set-if-None preserves the original delivery date even if
        # an admin later toggles status back and forth, so the return window is
        # never silently reset. Stored tz-aware (UTC) to match the column and to
        # compare cleanly against a timezone-aware "now" in Stage-3 eligibility
        # checks (avoids the naive/aware TypeError we hit in Phase 11).
        if (status or "").lower() == "delivered" and db_order.delivered_at is None:
            db_order.delivered_at = datetime.now(timezone.utc)

        # B-5: add notification rows in the same transaction before committing
        _try_emit_status_notification(db, db_order, status)

        db.commit()
        db.refresh(db_order)
        return db_order
    except Exception as e:
        db.rollback()
        raise e


# ── Shipping integration (Phase 14) ────────────────────────────────
# Single source of truth for writing an order's status from the shipping
# subsystem. Reuses the delivered-stamp behaviour so the return window starts
# correctly whether delivery is marked from the Orders page or the Shipping page.
# Writes the EXACT lowercase status strings the order flow already uses.
def apply_order_status_sync(db: Session, order_id: str, new_status: str) -> "Order":
    """Set an order's status (and stamp delivered_at once, set-if-None) from a
    shipment transition. Does NOT commit — caller owns the transaction so the
    shipment + order changes commit atomically together."""
    db_order = db.query(Order).filter(Order.id == order_id).first()
    if not db_order:
        return None
    db_order.status     = new_status
    db_order.updated_at = datetime.now(timezone.utc)
    if (new_status or "").lower() == "delivered" and db_order.delivered_at is None:
        db_order.delivered_at = datetime.now(timezone.utc)
    return db_order
