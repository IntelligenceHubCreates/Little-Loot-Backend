"""Step 22: bulk order enquiries (quote requests, 25+ units)

Revision ID: step22_bulk_enquiries
Revises: step21_product_sku
Create Date: 2026-10-30

Idempotent: CREATE TABLE / INDEX IF NOT EXISTS.
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = 'step22_bulk_enquiries'
down_revision: Union[str, None] = 'step21_product_sku'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS bulk_enquiries (
            id            UUID PRIMARY KEY,
            product_id    UUID NULL REFERENCES products(id) ON DELETE SET NULL,
            product_name  VARCHAR(255) NULL,
            product_sku   VARCHAR(20)  NULL,
            user_id       UUID NULL REFERENCES users(id) ON DELETE SET NULL,
            quantity      INTEGER NOT NULL,
            name          VARCHAR(120) NOT NULL,
            phone         VARCHAR(20)  NOT NULL,
            email         VARCHAR(255) NULL,
            city          VARCHAR(120) NULL,
            needed_by     DATE NULL,
            personalise   VARCHAR(10)  NULL,
            notes         TEXT NULL,
            status        VARCHAR(20)  NOT NULL DEFAULT 'new',
            quoted_price  DECIMAL(20, 2) NULL,
            admin_notes   TEXT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """))
    for col in ("product_id", "user_id", "status", "created_at"):
        op.execute(sa.text(f"CREATE INDEX IF NOT EXISTS ix_bulk_enquiries_{col} ON bulk_enquiries ({col});"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS bulk_enquiries;"))
