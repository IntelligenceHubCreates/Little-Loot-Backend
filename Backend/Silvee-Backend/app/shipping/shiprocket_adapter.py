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