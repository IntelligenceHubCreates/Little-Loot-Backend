"""Step 28: hide products that have no photo

Revision ID: step28_hide_noimg_products
Revises: step27_reel_video
Create Date: 2026-11-14

One-off data change: every product still without a photo is hidden from the
shop (is_active = false). Nothing is deleted — the admin can show a product
again from its edit form ("Visible on site") once a photo is uploaded.
The ids hidden here are recorded so the downgrade only un-hides those.
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = 'step28_hide_noimg_products'
down_revision: Union[str, None] = 'step27_reel_video'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS products_hidden_step28 (
            id UUID PRIMARY KEY,
            hidden_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """))
    op.execute(sa.text("""
        INSERT INTO products_hidden_step28 (id)
        SELECT p.id FROM products p
        WHERE p.is_active IS TRUE
          AND (
                p.product_image IS NULL
             OR json_typeof(p.product_image::json) <> 'array'
             OR json_array_length(p.product_image::json) = 0
          )
        ON CONFLICT (id) DO NOTHING;
    """))
    op.execute(sa.text("""
        UPDATE products SET is_active = FALSE
        WHERE id IN (SELECT id FROM products_hidden_step28) AND is_active IS TRUE;
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF to_regclass('products_hidden_step28') IS NOT NULL THEN
                UPDATE products SET is_active = TRUE
                WHERE id IN (SELECT id FROM products_hidden_step28);
                DROP TABLE products_hidden_step28;
            END IF;
        END $$;
    """))
