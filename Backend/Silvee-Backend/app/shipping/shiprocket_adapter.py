# app/shipping/shiprocket_adapter.py
"""
Shiprocket adapter (Phase 14, Stage 2a). STATUS: STUB.

Defines the surface the shipping service will call to AUTO-transmit to the
courier (create order → assign AWB → generate label → schedule pickup) and the
shape the inbound webhook maps from. All functions are stubs that raise
NotConfigured until SHIPROCKET_API_EMAIL/PASSWORD exist and the real calls are
filled in (against Shiprocket's CURRENT API, web-searched at activation time).

The manual flow (Stage 2b services) does NOT depend on this — manual assign of
courier/AWB/label works without any of it. This module is only the seam for
the later 'activate automation' pass.
"""
from __future__ import annotations

import os
from typing import Optional

SHIPROCKET_API_EMAIL    = os.getenv("SHIPROCKET_API_EMAIL", "")
SHIPROCKET_API_PASSWORD = os.getenv("SHIPROCKET_API_PASSWORD", "")
SHIPROCKET_WEBHOOK_TOKEN = os.getenv("SHIPROCKET_WEBHOOK_TOKEN", "")
SHIPROCKET_BASE = "https://apiv2.shiprocket.in/v1/external"


class NotConfigured(Exception):
    pass


def is_configured() -> bool:
    return bool(SHIPROCKET_API_EMAIL and SHIPROCKET_API_PASSWORD)


def _require():
    if not is_configured():
        raise NotConfigured("Shiprocket API credentials are not set; use manual courier assignment.")


def get_token() -> str:
    """POST /auth/login → token. Cache with TTL when implemented."""
    _require()
    raise NotConfigured("Shiprocket auth not yet activated.")
    # WHEN LIVE:
    # import requests
    # r = requests.post(f"{SHIPROCKET_BASE}/auth/login",
    #                   json={"email": SHIPROCKET_API_EMAIL, "password": SHIPROCKET_API_PASSWORD}, timeout=20)
    # r.raise_for_status(); return r.json()["token"]


def create_shipment(shipment_dict: dict) -> dict:
    """Create the courier order + assign AWB + (optionally) label. Returns
    {awb_number, courier_name, label_url, tracking_url, shiprocket_order_id}."""
    _require()
    raise NotConfigured("Shiprocket create_shipment not yet activated.")


def schedule_pickup(shiprocket_order_id: str) -> dict:
    _require()
    raise NotConfigured("Shiprocket schedule_pickup not yet activated.")


# ═══════════════════════════════════════════════════════════════════
# LIVE: reverse pickup for damage reports (return order → AWB)
# ═══════════════════════════════════════════════════════════════════
# Uses Shiprocket's external API with an API user (Shiprocket → Settings → API →
# Configure → Create an API User). Kept separate from the forward-shipping stubs
# above so activating returns doesn't change outgoing-order behaviour.

import time
import logging

logger = logging.getLogger(__name__)

_TOKEN_TTL_SECONDS = 8 * 24 * 3600     # Shiprocket tokens last 10 days; refresh early
_token_cache: dict = {"token": None, "at": 0.0}


class ShiprocketError(Exception):
    """A Shiprocket call failed; the message is safe to show to the admin."""


def _error_text(resp) -> str:
    """Pull a readable message out of a Shiprocket error response."""
    try:
        j = resp.json()
    except Exception:
        return f"HTTP {resp.status_code}"
    msg = j.get("message") if isinstance(j, dict) else None
    errs = j.get("errors") if isinstance(j, dict) else None
    if isinstance(errs, dict) and errs:
        parts = []
        for k, v in errs.items():
            parts.append(f"{k}: {v[0] if isinstance(v, list) and v else v}")
        return (msg + " — " if msg else "") + "; ".join(parts)
    return str(msg or f"HTTP {resp.status_code}")


def _token(force: bool = False) -> str:
    import requests
    if not is_configured():
        raise ShiprocketError("Shiprocket is not connected (SHIPROCKET_API_EMAIL / SHIPROCKET_API_PASSWORD not set).")
    if not force and _token_cache["token"] and time.time() - _token_cache["at"] < _TOKEN_TTL_SECONDS:
        return _token_cache["token"]
    try:
        r = requests.post(f"{SHIPROCKET_BASE}/auth/login",
                          json={"email": SHIPROCKET_API_EMAIL, "password": SHIPROCKET_API_PASSWORD}, timeout=20)
    except requests.RequestException as exc:
        raise ShiprocketError(f"Could not reach Shiprocket: {exc}")
    if r.status_code != 200 or not (r.json() or {}).get("token"):
        raise ShiprocketError(f"Shiprocket login failed: {_error_text(r)}")
    _token_cache.update(token=r.json()["token"], at=time.time())
    return _token_cache["token"]


def _post(path: str, payload: dict) -> dict:
    """POST with the cached token; re-login once on 401."""
    import requests
    for attempt in (0, 1):
        try:
            r = requests.post(f"{SHIPROCKET_BASE}{path}", json=payload, timeout=30,
                              headers={"Authorization": f"Bearer {_token(force=attempt == 1)}"})
        except requests.RequestException as exc:
            raise ShiprocketError(f"Could not reach Shiprocket: {exc}")
        if r.status_code == 401 and attempt == 0:
            continue
        if r.status_code >= 400:
            raise ShiprocketError(_error_text(r))
        try:
            return r.json() or {}
        except Exception:
            return {}
    raise ShiprocketError("Shiprocket authentication failed")


def tracking_url_for(awb: str) -> str:
    return f"https://shiprocket.co/tracking/{awb}" if awb else ""


def create_return_order(payload: dict) -> dict:
    """POST /orders/create/return → {'order_id', 'shipment_id'} (as strings)."""
    data = _post("/orders/create/return", payload)
    order_id = data.get("order_id")
    shipment_id = data.get("shipment_id")
    if not shipment_id:
        raise ShiprocketError(f"Shiprocket did not create the return order: {data.get('message') or data}")
    return {"order_id": str(order_id or ""), "shipment_id": str(shipment_id)}


def assign_return_awb(shipment_id: str) -> dict:
    """POST /courier/assign/awb with is_return=1. Shiprocket picks the courier and
    schedules the pickup from the customer's address. Returns
    {'awb', 'courier_name', 'pickup_date'(str|None)}."""
    data = _post("/courier/assign/awb", {"shipment_id": int(shipment_id), "is_return": 1})
    inner = ((data.get("response") or {}).get("data")) or {}
    awb = inner.get("awb_code") or data.get("awb_code")
    if not awb:
        raise ShiprocketError(
            "Return order created in Shiprocket, but no courier was assigned yet: "
            f"{data.get('message') or inner.get('awb_assign_error') or 'no serviceable courier'}. "
            "You can retry, or assign one in the Shiprocket dashboard and enter the AWB manually."
        )
    return {
        "awb": str(awb),
        "courier_name": inner.get("courier_name") or "",
        "pickup_date": inner.get("pickup_scheduled_date"),
    }


def verify_webhook(token_header: Optional[str]) -> bool:
    """Shiprocket sends the configured token in the 'x-api-key' header."""
    if not SHIPROCKET_WEBHOOK_TOKEN:
        return False
    return token_header == SHIPROCKET_WEBHOOK_TOKEN


# Maps a Shiprocket webhook status string → our shipment state. Filled at
# activation (their statuses: 'PICKED UP', 'IN TRANSIT', 'OUT FOR DELIVERY',
# 'DELIVERED', 'RTO INITIATED', etc.). Kept here so the webhook (Stage 3) is thin.
SHIPROCKET_STATUS_MAP = {
    "PICKED UP":         "picked_up",
    "IN TRANSIT":        "in_transit",
    "OUT FOR DELIVERY":  "out_for_delivery",
    "DELIVERED":         "delivered",
    "RTO INITIATED":     "rto_initiated",
    "RTO DELIVERED":     "returned_to_origin",
    "CANCELLED":         "cancelled",
}