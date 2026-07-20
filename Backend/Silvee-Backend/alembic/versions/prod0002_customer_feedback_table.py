"""Add customer_feedback table

Revision ID: prod0002
Revises: prod0001
Create Date: 2026-07-20

Idempotent — uses CREATE TABLE IF NOT EXISTS.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'prod0002'
down_revision: Union[str, None] = 'prod0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS customer_feedback (
            id             UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_name  VARCHAR(120) NOT NULL,
            image_url      VARCHAR(600),
            video_url      VARCHAR(600),
            thumbnail_url  VARCHAR(600),
            caption        TEXT,
            is_active      BOOLEAN      NOT NULL DEFAULT TRUE,
            display_order  INTEGER      NOT NULL DEFAULT 0,
            created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_customer_feedback_is_active ON customer_feedback (is_active);"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_customer_feedback_display_order ON customer_feedback (display_order);"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS customer_feedback CASCADE;"))
