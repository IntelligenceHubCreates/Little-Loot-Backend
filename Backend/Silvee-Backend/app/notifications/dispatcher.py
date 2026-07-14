# app/notifications/dispatcher.py
"""
Notification dispatcher (Phase 14, Stage 2a).

emit_notification(...) is the single entry point the shipping service calls at
each lifecycle transition. It:
  1. writes a REAL in-app Notification row (the customer bell reads these),
  2. calls the WhatsApp adapter (stub → logs 'skipped_no_provider' today),
  3. records a ShipmentNotificationLog row per channel (audit).

Never raises: a notification failure must NOT break a shipment transition. It
does NOT commit — the caller's transaction owns the commit so notification rows
land atomically with the shipment/order change.
"""
from __future__ import annotations

from typing import Optional, List
from sqlalchemy.orm import Session

from app.notifications.models import Notification, ShipmentNotificationLog
from app.notifications.whatsapp_adapter import send_whatsapp, WHATSAPP_PROVIDER

# Per-event customer-facing copy + which channels to attempt.
# {0}=order short id, {1}=courier, {2}=awb, {3}=tracking_url
EVENT_CONFIG = {
    "packed":            {"type": "shipping", "title": "Order packed 📦",
                          "body": "Order {0} has been packed and is ready to ship.",
                          "wa_template": "order_packed", "channels": ["in_app", "whatsapp"]},
    "shipped":           {"type": "shipping", "title": "Order shipped 🚚",
                          "body": "Order {0} has shipped via {1}. Track it anytime.",
                          "wa_template": "order_shipped", "channels": ["in_app", "whatsapp"]},
    "out_for_delivery":  {"type": "shipping", "title": "Out for delivery 🛵",
                          "body": "Order {0} is out for delivery today!",
                          "wa_template": "order_out_for_delivery", "channels": ["in_app", "whatsapp"]},
    "delivered":         {"type": "shipping", "title": "Delivered 🎉",
                          "body": "Order {0} has been delivered. Enjoy!",
                          "wa_template": "order_delivered", "channels": ["in_app", "whatsapp"]},
    "delivery_failed":   {"type": "shipping", "title": "Delivery attempt failed",
                          "body": "We couldn't deliver order {0}. We'll try again soon.",
                          "wa_template": "delivery_failed", "channels": ["in_app", "whatsapp"]},
    "rto_initiated":     {"type": "shipping", "title": "Return to origin started",
                          "body": "Order {0} is being returned to us. Support will reach out.",
                          "wa_template": None, "channels": ["in_app"]},
    "return_pickup_scheduled": {"type": "shipping", "title": "Return pickup scheduled",
                          "body": "A pickup for your return of order {0} has been scheduled.",
                          "wa_template": "return_pickup_scheduled", "channels": ["in_app", "whatsapp"]},
}


def _fmt(s: str, args: List[str]) -> str:
    out = s
    for i, a in enumerate(args):
        out = out.replace("{" + str(i) + "}", str(a or ""))
    return out


def emit_notification(
    db: Session,
    *,
    event: str,
    user_id,
    order_id=None,
    shipment_id=None,
    phone_e164: Optional[str] = None,
    courier_name: str = "",
    awb_number: str = "",
    tracking_url: str = "",
    link: Optional[str] = None,
) -> None:
    cfg = EVENT_CONFIG.get(event)
    if not cfg:
        return  # unknown event → no-op (never raise)

    order_short = (str(order_id)[:8].upper() if order_id else "")
    vars_list = [order_short, courier_name, awb_number, tracking_url]
    title = cfg["title"]
    body  = _fmt(cfg["body"], vars_list)
    deep_link = link or (f"/track-order?order_id={order_id}" if order_id else None)

    try:
        # 1) Real in-app notification (the bell reads this)
        if "in_app" in cfg["channels"] and user_id:
            db.add(Notification(
                user_id=user_id, type=cfg["type"], title=title, body=body,
                link=deep_link,
                meta={"event": event, "order_id": str(order_id) if order_id else None,
                      "shipment_id": str(shipment_id) if shipment_id else None,
                      "awb": awb_number or None},
            ))
            db.add(ShipmentNotificationLog(
                shipment_id=shipment_id, user_id=user_id, event=event,
                channel="in_app", status="sent", payload={"title": title, "body": body},
            ))

        # 2) WhatsApp (stub today → logs skipped_no_provider; real once activated)
        if "whatsapp" in cfg["channels"] and cfg.get("wa_template") and phone_e164:
            status, ref, err, payload = send_whatsapp(phone_e164, cfg["wa_template"], vars_list)
            db.add(ShipmentNotificationLog(
                shipment_id=shipment_id, user_id=user_id, event=event,
                channel="whatsapp", status=status, provider=WHATSAPP_PROVIDER,
                provider_ref=ref, error=err, payload=payload,
            ))
    except Exception:
        # Swallow — notifications must never break the shipment transition.
        # (No re-raise; the caller commits regardless.)
        pass