"""Step 17: Add gift_to, gift_from, hide_prices columns to orders

Revision ID: step17_gift_order_columns
Revises: step13b_tag_products_data
Create Date: 2026-10-23

Idempotent: each ALTER is wrapped in a duplicate_column guard.
gift_message already exists (varchar 500) — not touched here.
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = 'step17_gift_order_columns'
down_revision: Union[str, None] = 'step13b_tag_products_data'
branch_labels = None
depends_on = None


def _add_col(table: str, col_sql: str) -> None:
    op.execute(sa.text(f"""
        DO $$ BEGIN
            ALTER TABLE {table} ADD COLUMN {col_sql};
        EXCEPTION
            WHEN duplicate_column THEN NULL;
        END $$;
    """))


def upgrade() -> None:
    _add_col('orders', "gift_to   VARCHAR(24)  NULL")
    _add_col('orders', "gift_from VARCHAR(24)  NULL")
    _add_col('orders', "hide_prices BOOLEAN    NOT NULL DEFAULT false")


def downgrade() -> None:
    for col in ['gift_to', 'gift_from', 'hide_prices']:
        op.execute(sa.text(
            f"ALTER TABLE orders DROP COLUMN IF EXISTS {col};"
        ))
