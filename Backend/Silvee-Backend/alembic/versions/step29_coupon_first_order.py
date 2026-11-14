"""Step 29: first-order coupons + the always-on FIRST10 coupon

Revision ID: step29_coupon_first_order
Revises: step28_hide_noimg_products
Create Date: 2026-11-14

- coupons.first_order_only: the coupon only works on a customer's first order
  (no earlier order that wasn't cancelled).
- max_uses = 0 now means "no usage limit".
- Seeds FIRST10: 10% off, no minimum order, no expiry, no usage limit, first
  order only. Skipped if a coupon with that code already exists.
Idempotent.
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = 'step29_coupon_first_order'
down_revision: Union[str, None] = 'step28_hide_noimg_products'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE coupons ADD COLUMN IF NOT EXISTS first_order_only BOOLEAN NOT NULL DEFAULT FALSE;"
    ))
    op.execute(sa.text("""
        INSERT INTO coupons (id, code, discount_type, discount_value, min_order, max_uses,
                             used_count, is_active, expires_at, created_at, first_order_only)
        SELECT md5(random()::text || clock_timestamp()::text)::uuid,
               'FIRST10', 'percentage', 10, 0, 0, 0, TRUE, NULL, now(), TRUE
        WHERE NOT EXISTS (SELECT 1 FROM coupons WHERE code = 'FIRST10');
    """))


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM coupons WHERE code = 'FIRST10' AND first_order_only IS TRUE AND used_count = 0;"))
    op.execute(sa.text("ALTER TABLE coupons DROP COLUMN IF EXISTS first_order_only;"))
