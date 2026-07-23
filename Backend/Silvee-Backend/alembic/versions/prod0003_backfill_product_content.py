"""Backfill product descriptions and detail bullets from stock report content.

Revision ID: prod0003
Revises: prod0002
Create Date: 2026-07-23

Safe / idempotent: only updates rows where details is NULL or empty array.
Only processes AUTO_IMPORT rows from little_loot_product_content.json.
Matches by LOWER(TRIM(name)) — case-insensitive exact match on source_name.
"""

from __future__ import annotations

import json
import os
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "prod0003"
down_revision: Union[str, None] = "prod0002"
branch_labels = None
depends_on = None


def _array_sql(items: list[str]) -> str:
    """Build ARRAY['a','b',...] literal for direct embedding in SQL.

    Using :param::text[] after a bind parameter breaks psycopg2's parser,
    so we embed the array literal directly. Values are from our own JSON only.
    Single-quotes are doubled (SQL standard escaping).
    """
    escaped = [f"'{item.replace(chr(39), chr(39) * 2)}'" for item in items]
    return "ARRAY[" + ", ".join(escaped) + "]"


def upgrade() -> None:
    data_path = os.path.join(
        os.path.dirname(__file__), "..", "data", "little_loot_product_content.json"
    )
    with open(data_path, encoding="utf-8") as fh:
        content = json.load(fh)

    products = content["products"]
    auto_import = [p for p in products if p.get("publish_action") == "AUTO_IMPORT"]

    conn = op.get_bind()
    updated = 0
    no_match: list[str] = []

    for product in auto_import:
        source_name: str = product["source_name"].strip()
        description: str = product.get("description", "").strip()
        details: list[str] = [str(d).strip() for d in product.get("details", []) if d]

        if not description or not details:
            continue

        # Embed array literal directly — avoids :param::text[] psycopg2 parse bug
        array_literal = _array_sql(details)

        result = conn.execute(
            sa.text(
                f"""
                UPDATE products
                SET    description = :description,
                       details     = {array_literal}
                WHERE  LOWER(TRIM(name)) = LOWER(TRIM(:source_name))
                  AND  (
                       details IS NULL
                    OR cardinality(details) = 0
                    OR EXISTS (SELECT 1 FROM unnest(details) d WHERE d LIKE 'Detail point%%')
                    OR description LIKE '%%available at Little Loot%%'
                  )
                """
            ),
            {
                "description": description,
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

    print(f"[prod0003] Updated {updated} products from {len(auto_import)} AUTO_IMPORT entries.")
    if no_match:
        print(
            f"[prod0003] {len(no_match)} source_name(s) had no matching product in DB "
            f"(first 20): {no_match[:20]}"
        )


def downgrade() -> None:
    # Data-only migration; intentionally not reversible.
    pass
