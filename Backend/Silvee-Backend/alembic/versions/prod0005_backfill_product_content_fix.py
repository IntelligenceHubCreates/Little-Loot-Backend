"""Backfill product descriptions and detail bullets — fixed SQL approach.

Revision ID: prod0005
Revises: prod0004
Create Date: 2026-07-23

prod0003 and prod0004 crashed with:
  psycopg2.errors.SyntaxError: syntax error at or near ":"
  details = :details::text[]

SQLAlchemy's text() parser is confused when a PostgreSQL cast (::) immediately
follows a named bind parameter. Fix: embed the ARRAY[...] literal directly in
the SQL string (values are our own trusted data, properly single-quote-escaped)
and only use bind params for plain scalar strings.

Covers:
  - Rows where details is NULL or empty (prod0003 target)
  - Rows where details/description still carry placeholder text (prod0004 target)
"""

from __future__ import annotations

import json
import os
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "prod0005"
down_revision: Union[str, None] = "prod0004"
branch_labels = None
depends_on = None


def _array_sql(items: list[str]) -> str:
    """Build an ARRAY['a','b',...] literal safe for embedding in SQL.

    Single-quotes inside each item are doubled (SQL standard escaping).
    Values come exclusively from our own product-content JSON, so
    embedding them directly is safe.
    """
    escaped = [f"'{item.replace(chr(39), chr(39) * 2)}'" for item in items]
    return "ARRAY[" + ", ".join(escaped) + "]"


def upgrade() -> None:
    # prod0003 was fixed directly. This migration is a no-op.
    print("[prod0005] No-op: prod0003 handles all backfill logic.")
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

        # Embed the array literal directly — avoids :param::text[] parse bug
        array_literal = _array_sql(details)

        sql = sa.text(
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
        )

        result = conn.execute(
            sql,
            {"description": description, "source_name": source_name},
        )

        if result.rowcount > 0:
            updated += result.rowcount
        else:
            exists = conn.execute(
                sa.text(
                    "SELECT 1 FROM products"
                    " WHERE LOWER(TRIM(name)) = LOWER(TRIM(:name)) LIMIT 1"
                ),
                {"name": source_name},
            ).fetchone()
            if not exists:
                no_match.append(source_name)

    print(
        f"[prod0005] Updated {updated} products from {len(auto_import)} AUTO_IMPORT entries."
    )
    if no_match:
        print(
            f"[prod0005] {len(no_match)} source_name(s) had no DB match"
            f" (first 20): {no_match[:20]}"
        )


def downgrade() -> None:
    pass
