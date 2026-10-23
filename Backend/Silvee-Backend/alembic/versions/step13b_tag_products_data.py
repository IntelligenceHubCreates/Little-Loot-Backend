"""Step 13b: Auto-tag products with recipients and occasions

Revision ID: step13b_tag_products_data
Revises: step14_taxonomy_merge
Create Date: 2026-10-23

Idempotent: every rule guards with NOT (tag = ANY(col)) so re-running
adds nothing if tags are already present.

Runs after step14 so category strings are already in their canonical
form (Bags & Pouches, Beauty & Hair, etc.).
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = 'step13b_tag_products_data'
down_revision: Union[str, None] = 'step14_taxonomy_merge'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Recipients ─────────────────────────────────────────────────

    # little (0–8): toys, stationery, bags
    conn.execute(sa.text("""
        UPDATE products
        SET recipients = array_append(recipients, 'little')
        WHERE is_active = true
          AND NOT ('little' = ANY(recipients))
          AND (
            category ILIKE '%toy%'
            OR category ILIKE '%stationery%'
            OR sub_category_slug IN ('art-craft','diy-creative','activity',
                                     'puzzles','board-games')
            OR category IN ('Bags','Bags & Pouches')
          )
    """))

    # school (6–12): stationery, bags, bottles
    conn.execute(sa.text("""
        UPDATE products
        SET recipients = array_append(recipients, 'school')
        WHERE is_active = true
          AND NOT ('school' = ANY(recipients))
          AND (
            category ILIKE '%stationery%'
            OR category IN ('Bags','Bags & Pouches','Bottles','Bottles & Lunch')
            OR sub_category_slug IN ('board-games','diy-creative','activity','puzzles',
                                     'pencil-pouches','lunch-bags','bags')
          )
    """))

    # tween (10–14): gadgets, keychains, phone charms, nails
    conn.execute(sa.text("""
        UPDATE products
        SET recipients = array_append(recipients, 'tween')
        WHERE is_active = true
          AND NOT ('tween' = ANY(recipients))
          AND (
            sub_category_slug IN ('watches-gadgets','keychains','phone-charms',
                                  'nails-jewellery')
            OR category IN ('Keychains','Phone Charms')
          )
    """))

    # her: beauty, gift sets, bags, keychains, phone charms
    conn.execute(sa.text("""
        UPDATE products
        SET recipients = array_append(recipients, 'her')
        WHERE is_active = true
          AND NOT ('her' = ANY(recipients))
          AND (
            category IN ('Beauty','Beauty & Hair','Gift Sets','Bags','Bags & Pouches',
                         'Keychains','Phone Charms','Gifts')
            OR sub_category_slug IN ('bath-body','hair','hygiene','nails-jewellery',
                                     'gift-sets','trolley','keychains','phone-charms')
          )
    """))

    # him: board-games, watches-gadgets, keychains
    conn.execute(sa.text("""
        UPDATE products
        SET recipients = array_append(recipients, 'him')
        WHERE is_active = true
          AND NOT ('him' = ANY(recipients))
          AND (
            sub_category_slug IN ('board-games','watches-gadgets','keychains')
            OR category = 'Keychains'
          )
    """))

    # baby (0–2): soft toys, activity
    conn.execute(sa.text("""
        UPDATE products
        SET recipients = array_append(recipients, 'baby')
        WHERE is_active = true
          AND NOT ('baby' = ANY(recipients))
          AND sub_category_slug IN ('activity','diy-creative')
          AND category ILIKE '%toy%'
    """))

    # ── Occasions ──────────────────────────────────────────────────

    # school: stationery, bags, bottles
    conn.execute(sa.text("""
        UPDATE products
        SET occasions = array_append(occasions, 'school')
        WHERE is_active = true
          AND NOT ('school' = ANY(occasions))
          AND (
            category ILIKE '%stationery%'
            OR category IN ('Bags','Bags & Pouches','Bottles','Bottles & Lunch')
            OR sub_category_slug IN ('pencil-pouches','lunch-bags','bags',
                                     'erasers','sharpeners','pens-pencils','art-craft')
          )
    """))

    # return: small giveaways
    conn.execute(sa.text("""
        UPDATE products
        SET occasions = array_append(occasions, 'return')
        WHERE is_active = true
          AND NOT ('return' = ANY(occasions))
          AND (
            category IN ('Keychains','Phone Charms')
            OR sub_category_slug IN ('erasers','sharpeners','pens-pencils',
                                     'keychains','phone-charms')
          )
    """))

    # birthday: toys, gift sets, bags, beauty, gadgets
    conn.execute(sa.text("""
        UPDATE products
        SET occasions = array_append(occasions, 'birthday')
        WHERE is_active = true
          AND NOT ('birthday' = ANY(occasions))
          AND (
            category ILIKE '%toy%'
            OR category IN ('Gift Sets','Gifts','Bags','Bags & Pouches',
                            'Beauty','Beauty & Hair','Keychains','Phone Charms')
            OR sub_category_slug IN ('gift-sets','trolley','activity','diy-creative',
                                     'puzzles','board-games','watches-gadgets',
                                     'duffle-travel','keychains','phone-charms')
          )
    """))

    # just because: beauty, keychains, gift sets
    conn.execute(sa.text("""
        UPDATE products
        SET occasions = array_append(occasions, 'just')
        WHERE is_active = true
          AND NOT ('just' = ANY(occasions))
          AND (
            category IN ('Beauty','Beauty & Hair','Keychains','Phone Charms',
                         'Gift Sets','Gifts')
            OR sub_category_slug IN ('bath-body','hair','hygiene','nails-jewellery',
                                     'keychains','phone-charms','gift-sets','trolley')
          )
    """))


def downgrade() -> None:
    conn = op.get_bind()
    for tag in ('little', 'school', 'tween', 'her', 'him', 'baby'):
        conn.execute(sa.text(
            "UPDATE products SET recipients = array_remove(recipients, :t) "
            "WHERE is_active = true"
        ), {"t": tag})
    for tag in ('school', 'return', 'birthday', 'just'):
        conn.execute(sa.text(
            "UPDATE products SET occasions = array_remove(occasions, :t) "
            "WHERE is_active = true"
        ), {"t": tag})
