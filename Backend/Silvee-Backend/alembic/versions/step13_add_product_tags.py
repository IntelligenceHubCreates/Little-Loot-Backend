"""Add recipients, occasions, pairs_with to products

Revision ID: step13_product_tags
Revises: prod0001
Create Date: 2026-10-23

Idempotent: every ALTER is wrapped in a duplicate_column guard.
Nothing is dropped or truncated.
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = 'step13_product_tags'
down_revision: Union[str, None] = 'prod0005'
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
    _add_col('products', "recipients TEXT[]    NOT NULL DEFAULT '{}'")
    _add_col('products', "occasions  TEXT[]    NOT NULL DEFAULT '{}'")
    _add_col('products', "pairs_with UUID[]    NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    for col in ['pairs_with', 'occasions', 'recipients']:
        op.execute(sa.text(
            f"ALTER TABLE products DROP COLUMN IF EXISTS {col};"
        ))
