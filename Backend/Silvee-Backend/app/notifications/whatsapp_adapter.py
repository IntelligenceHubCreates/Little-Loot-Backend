# app/notifications/whatsapp_adapter.py
"""
WhatsApp adapter — AiSensy (Phase 14, Stage 2a).

STATUS: STUB. It builds the EXACT payload AiSensy's campaign API expects and
RETURNS it without sending, because sending requires:
  - WHATSAPP_API_KEY (AiSensy)         — your account
  - approved message templates          — Meta review
  - WHATSAPP_SENDER (business number)

When those exist in env, replace the marked block with the real HTTP call
(≈15 lines) — the payload shape here already matches AiSensy, so nothing else
changes. The dispatcher logs every attempt to shipment_notification_logs.

Template names expected (create + get approved in AiSensy):
  order_packed, order_shipped, order_out_for_delivery, order_delivered,
  delivery_failed, return_pickup_scheduled
"""
from __future__ import annotations

import os
from typing import List, Tuple, Optional

WHATSAPP_PROVIDER = os.getenv("WHATSAPP_PROVIDER", "aisensy")
WHATSAPP_API_KEY  = os.getenv("WHATSAPP_API_KEY", "")
WHATSAPP_SENDER   = os.getenv("WHATSAPP_SENDER", "")

AISENSY_CAMPAIGN_URL = "https://backend.aisensy.com/campaign/t1/api/v2"


def is_configured() -> bool:
    """True only when a real send is possible. Until then, callers log + skip."""
    return bool(WHATSAPP_API_KEY and WHATSAPP_SENDER)


def build_payload(phone_e164: str, template_name: str, variables: List[str],
                  campaign_name: Optional[str] = None) -> dict:
    """Construct the AiSensy v2 campaign payload. phone_e164 like '919876543210'."""
    return {
        "apiKey": WHATSAPP_API_KEY,                 # filled from env when live
        "campaignName": campaign_name or template_name,
        "destination": phone_e164,
        "userName": "Little Loot",
        "templateParams": [str(v) for v in variables],
        # "media": {...}  # add if a template uses header media (e.g. label image)
    }


def send_whatsapp(phone_e164: str, template_name: str, variables: List[str]) -> Tuple[str, Optional[str], Optional[str], dict]:
    """
    Returns (status, provider_ref, error, payload) for the dispatcher to log.
      status: 'sent' | 'skipped_no_provider' | 'failed'

    STUB BEHAVIOUR: builds the payload, returns 'skipped_no_provider' if not
    configured. Never raises — notification failure must never break a shipment
    transition.
    """
    payload = build_payload(phone_e164, template_name, variables)

    if not is_configured():
        return ("skipped_no_provider", None, None, payload)

    # ─── REPLACE THIS BLOCK WHEN LIVE (AiSensy) ────────────────────────────
    # import requests
    # try:
    #     r = requests.post(AISENSY_CAMPAIGN_URL, json=payload, timeout=20)
    #     if r.status_code in (200, 201):
    #         ref = (r.json() or {}).get("messageId") or ""
    #         return ("sent", ref, None, payload)
    #     return ("failed", None, f"HTTP {r.status_code}: {r.text[:300]}", payload)
    # except Exception as e:
    #     return ("failed", None, str(e), payload)
    # ───────────────────────────────────────────────────────────────────────

    # Until the block above is enabled, even a configured key short-circuits here
    # so we never half-send. Flip this to call the block when you go live.
    return ("skipped_no_provider", None, "adapter not yet activated", payload)