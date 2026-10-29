# app/products/customization.py
"""
Product personalisation (initials / name engraving).

Two shapes, both plain JSON:

  Product.customization  — the admin's settings for a product (normalize_config)
      {"enabled": true, "types": ["initials", "name"], "max_initials": 3,
       "max_name": 12, "price": 49, "fonts": ["classic", "script", "bold"],
       "required": false, "method": "engraved", "placement": "on the lid"}

  Cart/order line customization — what the shopper chose (validate_choice)
      {"type": "name", "text": "Aarav", "font": "script", "price": 49,
       "method": "engraved", "placement": "on the lid"}

The shopper's choice is ALWAYS re-validated against the product's current
settings on the server (cart add + payment create-order), and the surcharge
comes from the product, never from the client.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

from fastapi import HTTPException

TYPES   = ("initials", "name")
FONTS   = ("classic", "script", "bold")
METHODS = ("engraved", "printed", "embroidered")

MAX_INITIALS_LIMIT = 4
MAX_NAME_LIMIT     = 30
MAX_PRICE          = 5000

_NAME_PUNCT = set(" .'&-")


def _is_letter(ch: str) -> bool:
    # Letters plus combining marks, so Indic names (e.g. Hindi matras) are accepted
    return unicodedata.category(ch)[0] in ("L", "M")


def _valid_initials(text: str) -> bool:
    return bool(text) and all(_is_letter(ch) for ch in text)


def _valid_name(text: str) -> bool:
    return bool(text) and _is_letter(text[0]) and all(_is_letter(ch) or ch in _NAME_PUNCT for ch in text)


def _as_int(v: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def normalize_config(raw: Any) -> Optional[dict]:
    """Admin settings → clean dict, or None when personalisation is off."""
    if not raw or not isinstance(raw, dict) or not raw.get("enabled"):
        return None
    types = [t for t in (raw.get("types") or []) if t in TYPES] or ["initials", "name"]
    fonts = [f for f in (raw.get("fonts") or []) if f in FONTS] or ["classic"]
    method = raw.get("method") if raw.get("method") in METHODS else "engraved"
    placement = str(raw.get("placement") or "").strip()[:60]
    return {
        "enabled":      True,
        "types":        types,
        "max_initials": _as_int(raw.get("max_initials"), 3, 1, MAX_INITIALS_LIMIT),
        "max_name":     _as_int(raw.get("max_name"), 12, 2, MAX_NAME_LIMIT),
        "price":        _as_int(raw.get("price"), 0, 0, MAX_PRICE),
        "fonts":        fonts,
        "required":     bool(raw.get("required")),
        "method":       method,
        "placement":    placement,
    }


def config_of(product) -> Optional[dict]:
    return normalize_config(getattr(product, "customization", None))


def _clean_text(s: str) -> str:
    s = unicodedata.normalize("NFC", str(s or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] != "C")   # drop control chars
    return re.sub(r"\s+", " ", s).strip()


def validate_choice(product, raw: Any) -> Optional[dict]:
    """Shopper's personalisation for a line → normalized dict, or None if not
    personalised. Raises 400 with a shopper-friendly message when invalid."""
    cfg = config_of(product)
    name = getattr(product, "name", "This product")

    has_choice = isinstance(raw, dict) and _clean_text(raw.get("text", ""))
    if not has_choice:
        if cfg and cfg["required"]:
            raise HTTPException(status_code=400, detail=f"Please add your personalisation for {name}")
        return None
    if not cfg:
        raise HTTPException(status_code=400, detail=f"{name} can no longer be personalised. Please remove the personalisation and add it again.")

    ctype = raw.get("type")
    if ctype not in cfg["types"]:
        ctype = cfg["types"][0]
    text = _clean_text(raw.get("text", ""))

    if ctype == "initials":
        text = text.replace(" ", "").replace(".", "").upper()
        if not _valid_initials(text):
            raise HTTPException(status_code=400, detail="Initials can only contain letters")
        if len(text) > cfg["max_initials"]:
            raise HTTPException(status_code=400, detail=f"Initials can be up to {cfg['max_initials']} letters")
    else:
        if not _valid_name(text):
            raise HTTPException(status_code=400, detail="Names can only contain letters, spaces and . ' & -")
        if len(text) > cfg["max_name"]:
            raise HTTPException(status_code=400, detail=f"Names can be up to {cfg['max_name']} characters")

    font = raw.get("font") if raw.get("font") in cfg["fonts"] else cfg["fonts"][0]
    return {
        "type":      ctype,
        "text":      text,
        "font":      font,
        "price":     cfg["price"],
        "method":    cfg["method"],
        "placement": cfg["placement"],
    }


def line_key(c: Optional[dict]) -> str:
    """Identity of a personalisation for cart-line matching ('' = none)."""
    if not c:
        return ""
    return f"{c.get('type', '')}|{c.get('text', '')}|{c.get('font', '')}"


def surcharge(c: Optional[dict]) -> float:
    return float(c.get("price") or 0) if c else 0.0


def describe(c: Optional[dict]) -> str:
    """One-line human description, e.g. 'Name “Aarav” · Script font · engraved'."""
    if not c or not c.get("text"):
        return ""
    label = "Initials" if c.get("type") == "initials" else "Name"
    parts = [f"{label} “{c['text']}”", f"{str(c.get('font') or 'classic').title()} font"]
    if c.get("method"):
        parts.append(str(c["method"]) + (f" {c['placement']}" if c.get("placement") else ""))
    return " · ".join(parts)
