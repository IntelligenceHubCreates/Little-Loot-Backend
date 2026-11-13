"""Step 26: Instagram reels (home Reel Wall + per-product reels)

Revision ID: step26_instagram_reels
Revises: step25_address_landmark
Create Date: 2026-11-13

Admin pastes a reel link, optionally adds a caption/cover, links products and
chooses whether it shows on the home page. Idempotent.
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = 'step26_instagram_reels'
down_revision: Union[str, None] = 'step25_address_landmark'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS instagram_reels (
            id            UUID PRIMARY KEY,
            url           TEXT NOT NULL,
            shortcode     VARCHAR(40) NOT NULL,
            kind          VARCHAR(10) NOT NULL DEFAULT 'reel',
            caption       VARCHAR(200) NULL,
            cover_url     TEXT NULL,
            product_ids   UUID[] NOT NULL DEFAULT '{}',
            show_on_home  BOOLEAN NOT NULL DEFAULT true,
            is_active     BOOLEAN NOT NULL DEFAULT true,
            sort_order    INTEGER NOT NULL DEFAULT 0,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ux_instagram_reels_shortcode ON instagram_reels (shortcode);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_instagram_reels_product_ids ON instagram_reels USING GIN (product_ids);"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS instagram_reels;"))
