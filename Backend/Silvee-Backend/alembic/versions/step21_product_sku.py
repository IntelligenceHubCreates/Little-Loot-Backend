"""Step 21: human-friendly product codes (SKU), e.g. LL-STA-0042

Revision ID: step21_product_sku
Revises: step20_product_customization
Create Date: 2026-10-30

- products.sku VARCHAR(20), unique.
- Backfills every product without a code: prefix from its category (same rules
  as app/products/sku.py), numbered per prefix in creation order, continuing
  after any codes that already exist. Idempotent.
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = 'step21_product_sku'
down_revision: Union[str, None] = 'step20_product_customization'
branch_labels = None
depends_on = None


BACKFILL = r"""
WITH pref AS (
    SELECT id, created_at,
           CASE lower(trim(coalesce(category, '')))
               WHEN 'stationery'      THEN 'STA'
               WHEN 'bags & pouches'  THEN 'BAG'
               WHEN 'beauty & hair'   THEN 'BTY'
               WHEN 'bottles & lunch' THEN 'BTL'
               WHEN 'toys & games'    THEN 'TOY'
               WHEN 'keychains'       THEN 'KEY'
               WHEN 'gift sets'       THEN 'GFT'
               ELSE coalesce(nullif(upper(left(regexp_replace(coalesce(category, ''), '[^A-Za-z]', '', 'g'), 3)), ''), 'GEN')
           END AS p
    FROM products
    WHERE sku IS NULL
),
numbered AS (
    SELECT id, p,
           row_number() OVER (PARTITION BY p ORDER BY created_at NULLS LAST, id) AS n
    FROM pref
),
existing AS (
    SELECT split_part(sku, '-', 2) AS p, max(split_part(sku, '-', 3)::int) AS top
    FROM products
    WHERE sku ~ '^LL-[A-Z]{1,3}-[0-9]+$'
    GROUP BY 1
)
UPDATE products
SET sku = 'LL-' || numbered.p || '-' || lpad((numbered.n + coalesce(existing.top, 0))::text, 4, '0')
FROM numbered
LEFT JOIN existing ON existing.p = numbered.p
WHERE products.id = numbered.id;
"""


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE products ADD COLUMN IF NOT EXISTS sku VARCHAR(20) NULL;"))
    op.execute(sa.text(BACKFILL))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ux_products_sku ON products (sku);"))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ux_products_sku;"))
    op.execute(sa.text("ALTER TABLE products DROP COLUMN IF EXISTS sku;"))
