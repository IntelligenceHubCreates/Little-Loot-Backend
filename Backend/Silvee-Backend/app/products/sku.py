# app/products/sku.py
"""
Human-friendly product codes (SKUs) for admin and business use.

Format: LL-<CAT>-<NNNN>, e.g. LL-STA-0042 (Stationery #42).
- <CAT> comes from the product's category when the code is first assigned.
- Codes never change afterwards (even if the category does), so printed labels,
  spreadsheets and supplier notes stay valid.
- The Step 21 migration backfills existing products with the SAME prefix rules
  (keep CATEGORY_PREFIX in sync with alembic/versions/step21_product_sku.py).
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.orm import Session

CATEGORY_PREFIX = {
    "stationery":      "STA",
    "bags & pouches":  "BAG",
    "beauty & hair":   "BTY",
    "bottles & lunch": "BTL",
    "toys & games":    "TOY",
    "keychains":       "KEY",
    "gift sets":       "GFT",
}


def prefix_for(category: Optional[str]) -> str:
    key = (category or "").strip().lower()
    if key in CATEGORY_PREFIX:
        return CATEGORY_PREFIX[key]
    letters = re.sub(r"[^A-Za-z]", "", category or "").upper()[:3]
    return letters or "GEN"


def next_sku(session: Session, category: Optional[str]) -> str:
    """Next free code for the category's prefix (highest existing number + 1)."""
    from app.products.models import Product

    prefix = prefix_for(category)
    like = f"LL-{prefix}-%"
    highest = 0
    for (sku,) in session.query(Product.sku).filter(Product.sku.like(like)).all():
        tail = (sku or "").rsplit("-", 1)[-1]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"LL-{prefix}-{highest + 1:04d}"
