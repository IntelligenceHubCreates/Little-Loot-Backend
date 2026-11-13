"""Step 24: offline (WhatsApp + UPI) order payments

Revision ID: step24_whatsapp_order_payment
Revises: step23_delete_noimg_products
Create Date: 2026-11-13

Until Razorpay is live, customers place an order on the site and it is sent to
the store on WhatsApp; the store confirms, collects payment by UPI and marks it
paid in Admin. These columns track that payment on the order itself.

  payment_method     'whatsapp_upi' | 'razorpay' | NULL (older orders)
  payment_status     'unpaid' | 'paid'
  payment_reference  UPI transaction id / note entered by the admin
  paid_at            when the admin marked it paid
  stock_restored     stock was given back when the order was cancelled (once)

Idempotent: ADD COLUMN IF NOT EXISTS.
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = 'step24_whatsapp_order_payment'
down_revision: Union[str, None] = 'step23_delete_noimg_products'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in (
        "payment_method VARCHAR(30) NULL",
        "payment_status VARCHAR(20) NULL",
        "payment_reference VARCHAR(120) NULL",
        "paid_at TIMESTAMPTZ NULL",
        "stock_restored BOOLEAN NOT NULL DEFAULT false",
    ):
        op.execute(sa.text(f"ALTER TABLE orders ADD COLUMN IF NOT EXISTS {col};"))
    # Existing orders were all paid online through Razorpay
    op.execute(sa.text(
        "UPDATE orders SET payment_method = 'razorpay', payment_status = 'paid' "
        "WHERE payment_method IS NULL AND razorpay_payment_id IS NOT NULL;"
    ))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_orders_payment_status ON orders (payment_status);"))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_orders_payment_status;"))
    for col in ("stock_restored", "paid_at", "payment_reference", "payment_status", "payment_method"):
        op.execute(sa.text(f"ALTER TABLE orders DROP COLUMN IF EXISTS {col};"))
