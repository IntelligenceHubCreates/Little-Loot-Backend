# app/returns/razorpay_refund.py
"""
Razorpay refund gateway wrapper (Phase 13).

Calls the documented REST endpoint POST /v1/payments/{id}/refund directly so the
X-Refund-Idempotency header is exact and SDK-version-independent. Converts ₹→paise
with Decimal (no float drift) and maps every documented error to a clean
HTTPException. Returns (gateway_refund_id, gateway_status).

This module NEVER decides whether to refund — it only executes a refund that the
caller (process_refund, reached only from the admin endpoint) has already chosen.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional, Tuple

import requests
from fastapi import HTTPException

from app.settings import settings

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"


def create_gateway_refund(
    payment_id: str,
    amount,                       # Decimal or float, in rupees
    idempotency_key: str,         # >=10 chars, [A-Za-z0-9_-] — we pass return-refund-{uuid}
    speed: str = "normal",        # normal (5–7 days) | optimum (instant when possible)
    notes: Optional[dict] = None,
) -> Tuple[str, str]:
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Payment service is not configured.")
    if not payment_id:
        raise HTTPException(status_code=400, detail="This order has no captured online payment to refund.")

    # ₹ → paise via Decimal, no float rounding error
    paise = int((Decimal(str(amount)) * 100).quantize(Decimal("1")))
    if paise < 100:
        raise HTTPException(status_code=400, detail="Refund amount must be at least ₹1.")

    body = {"amount": paise, "speed": speed}
    if notes:
        body["notes"] = notes

    try:
        resp = requests.post(
            f"{RAZORPAY_API_BASE}/payments/{payment_id}/refund",
            json=body,
            headers={"X-Refund-Idempotency": idempotency_key},
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            timeout=30,
        )
    except requests.RequestException as e:
        # Network/timeout: refund state is UNKNOWN. Safe to retry — the same
        # idempotency key guarantees Razorpay won't create a second refund.
        raise HTTPException(status_code=502, detail=f"Could not reach the payment gateway ({e}). Safe to retry.")

    # 409: a request with this key is still processing — retry shortly.
    if resp.status_code == 409:
        raise HTTPException(status_code=409, detail="A refund for this return is already being processed. Try again shortly.")

    if resp.status_code not in (200, 201):
        try:
            desc = resp.json().get("error", {}).get("description") or resp.text
        except Exception:
            desc = resp.text or f"HTTP {resp.status_code}"
        # Covers: amount > captured, already fully refunded, invalid payment_id,
        # invalid key/secret, bad idempotency key — all surfaced verbatim to the admin.
        raise HTTPException(status_code=400, detail=f"Razorpay refund failed: {desc}")

    data = resp.json()
    return data.get("id"), data.get("status")   # ('rfnd_…', 'pending'|'processed'|'failed')