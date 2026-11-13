"""Step 27: optional uploaded video for Instagram reels

Revision ID: step27_reel_video
Revises: step26_instagram_reels
Create Date: 2026-11-13

When set, the Reel Wall plays this video natively (video only, no Instagram
header/footer); otherwise it falls back to the cropped Instagram embed.
Idempotent.
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = 'step27_reel_video'
down_revision: Union[str, None] = 'step26_instagram_reels'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE instagram_reels ADD COLUMN IF NOT EXISTS video_url TEXT NULL;"))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE instagram_reels DROP COLUMN IF EXISTS video_url;"))
