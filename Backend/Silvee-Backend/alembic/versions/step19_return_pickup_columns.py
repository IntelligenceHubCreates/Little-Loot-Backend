"""Step 19: pickup details on return_requests (damage-report reverse pickup)

Revision ID: step19_return_pickup_columns
Revises: step18_product_description_text
Create Date: 2026-10-29

Stores how a damaged item is collected from the customer: booked through
Shiprocket (return order + AWB) or entered manually by the admin.

Idempotent: ADD COLUMN IF NOT EXISTS.
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = 'step19_return_pickup_columns'
down_revision: Union[str, None] = 'step18_product_description_text'
branch_labels = None
depends_on = None

COLUMNS = [
    ("pickup_mode",           "VARCHAR(20)"),    # shiprocket | manual
    ("pickup_courier",        "VARCHAR(120)"),
    ("pickup_awb",            "VARCHAR(120)"),
    ("pickup_tracking_url",   "TEXT"),
    ("pickup_date",           "TIMESTAMPTZ"),
    ("pickup_sr_order_id",    "VARCHAR(40)"),    # Shiprocket return order id
    ("pickup_sr_shipment_id", "VARCHAR(40)"),    # Shiprocket shipment id (for AWB retry)
]


def upgrade() -> None:
    for name, sql_type in COLUMNS:
        op.execute(sa.text(f"ALTER TABLE return_requests ADD COLUMN IF NOT EXISTS {name} {sql_type} NULL;"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_return_requests_pickup_awb ON return_requests (pickup_awb);"))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_return_requests_pickup_awb;"))
    for name, _ in COLUMNS:
        op.execute(sa.text(f"ALTER TABLE return_requests DROP COLUMN IF EXISTS {name};"))
