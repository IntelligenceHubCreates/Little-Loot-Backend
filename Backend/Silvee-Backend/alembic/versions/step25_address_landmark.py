"""Step 25: landmark on saved addresses

Revision ID: step25_address_landmark
Revises: step24_whatsapp_order_payment
Create Date: 2026-11-13

Checkout collects a landmark, but user_addresses had no column for it, so it
was silently dropped. Idempotent.
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = 'step25_address_landmark'
down_revision: Union[str, None] = 'step24_whatsapp_order_payment'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE user_addresses ADD COLUMN IF NOT EXISTS landmark VARCHAR(255) NULL;"))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE user_addresses DROP COLUMN IF EXISTS landmark;"))
