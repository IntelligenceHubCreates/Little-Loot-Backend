"""Step 20: product personalisation (initials / name engraving)

Revision ID: step20_product_customization
Revises: step19_return_pickup_columns
Create Date: 2026-10-29

- products.customization       JSON  — admin settings (see app/products/customization.py)
- products.is_customizable     BOOL  — denormalised flag for the "Personalised" filter
- cart_items.customization     JSON  — the shopper's choice on a cart line
- order_items.customization    JSON  — the choice as ordered (text, font, surcharge)

Idempotent: ADD COLUMN IF NOT EXISTS.
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = 'step20_product_customization'
down_revision: Union[str, None] = 'step19_return_pickup_columns'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE products    ADD COLUMN IF NOT EXISTS customization JSON NULL;"))
    op.execute(sa.text("ALTER TABLE products    ADD COLUMN IF NOT EXISTS is_customizable BOOLEAN NOT NULL DEFAULT false;"))
    op.execute(sa.text("ALTER TABLE cart_items  ADD COLUMN IF NOT EXISTS customization JSON NULL;"))
    op.execute(sa.text("ALTER TABLE order_items ADD COLUMN IF NOT EXISTS customization JSON NULL;"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_products_is_customizable ON products (is_customizable);"))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_products_is_customizable;"))
    op.execute(sa.text("ALTER TABLE order_items DROP COLUMN IF EXISTS customization;"))
    op.execute(sa.text("ALTER TABLE cart_items  DROP COLUMN IF EXISTS customization;"))
    op.execute(sa.text("ALTER TABLE products    DROP COLUMN IF EXISTS is_customizable;"))
    op.execute(sa.text("ALTER TABLE products    DROP COLUMN IF EXISTS customization;"))
