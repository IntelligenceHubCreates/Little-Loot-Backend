"""Step 18: products.description VARCHAR(300) -> TEXT

Revision ID: step18_product_description_text
Revises: step17_gift_order_columns
Create Date: 2026-10-28

An early migration (4882f174ce31) created description as VARCHAR(300) with a
btree index; prod0001's "ADD COLUMN description TEXT" was skipped because the
column already existed. Saving a longer description raised a DB error (500).

Idempotent: ALTER TYPE to TEXT is a no-op if already TEXT; index drop uses IF EXISTS.
The index is dropped because nothing filters on description and btree entries
fail for very long values.
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = 'step18_product_description_text'
down_revision: Union[str, None] = 'step17_gift_order_columns'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_products_description;"))
    op.execute(sa.text("ALTER TABLE products ALTER COLUMN description TYPE TEXT;"))


def downgrade() -> None:
    # Not reverting to VARCHAR(300): it would fail (or truncate) for longer descriptions.
    pass
