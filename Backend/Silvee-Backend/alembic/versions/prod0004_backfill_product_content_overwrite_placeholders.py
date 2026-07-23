"""Overwrite placeholder product descriptions and detail bullets.

Revision ID: prod0004
Revises: prod0003
Create Date: 2026-07-23

prod0003 only updated rows where details was empty (cardinality = 0).
This migration also updates rows that still carry auto-generated placeholder
content:
  - description containing 'available at Little Loot'
  - details whose first element starts with 'Detail point'

Only processes AUTO_IMPORT rows. Idempotent: safe to run multiple times.
"""

from __future__ import annotations

import json
import os
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "prod0004"
down_revision: Union[str, None] = "prod0003"
branch_labels = None
depends_on = None


def _pg_array_str(items: list[str]) -> str:
    parts = []
    for item in items:
        safe = item.replace("\\", "\\\\").replace('"', '\\"')
        parts.append(f'"{safe}"')
    return "{" + ",".join(parts) + "}"


def upgrade() -> None:
    # prod0003 was fixed to handle both empty and placeholder rows.
    # This migration is intentionally a no-op.
    print("[prod0004] No-op: prod0003 handles all backfill logic.")
    return
    data_path = os.path.join(
        os.path.dirname(__file__), "..", "data", "little_loot_product_content.json"
    )
    with open(data_path, encoding="utf-8") as fh:
        content = json.load(fh)

    auto_import = [
        p for p in content["products"] if p.get("publish_action") == "AUTO_IMPORT"
    ]

    conn = op.get_bind()
    updated = 0
    no_match: list[str] = []

    for product in auto_import:
        source_name: str = product["source_name"].strip()
        description: str = product.get("description", "").strip()
        details: list[str] = [str(d).strip() for d in product.get("details", []) if d]

        if not description or not details:
            continue

        details_param = _pg_array_str(details)

        result = conn.execute(
            sa.text(
                """
                UPDATE products
                SET    description = :description,
                       details     = :details::text[]
                WHERE  LOWER(TRIM(name)) = LOWER(TRIM(:source_name))
                  AND (
                       details IS NULL
                    OR cardinality(details) = 0
                    OR EXISTS (
                           SELECT 1 FROM unnest(details) d
                           WHERE d LIKE 'Detail point%'
                       )
                    OR description LIKE '%available at Little Loot%'
                  )
                """
            ),
            {
                "description": description,
                "details": details_param,
                "source_name": source_name,
            },
        )

        if result.rowcount > 0:
            updated += result.rowcount
        else:
            exists = conn.execute(
                sa.text(
                    "SELECT 1 FROM products WHERE LOWER(TRIM(name)) = LOWER(TRIM(:name)) LIMIT 1"
                ),
                {"name": source_name},
            ).fetchone()
            if not exists:
                no_match.append(source_name)

    print(f"[prod0004] Updated {updated} products from {len(auto_import)} AUTO_IMPORT entries.")
    if no_match:
        print(
            f"[prod0004] {len(no_match)} source_name(s) had no DB match "
            f"(first 20): {no_match[:20]}"
        )


def downgrade() -> None:
    pass
