"""Step 14: Category taxonomy merge — rename legacy category strings, re-home Other products

Revision ID: step14_taxonomy_merge
Revises: step13_product_tags
Create Date: 2026-10-23

Idempotent: each UPDATE is guarded by WHERE category = '<old value>', so re-running
has no effect once names are already correct.
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = 'step14_taxonomy_merge'
down_revision: Union[str, None] = 'step13_product_tags'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Clean renames
    renames = [
        ('Bags',    'Bags & Pouches'),
        ('Beauty',  'Beauty & Hair'),
        ('Bottles', 'Bottles & Lunch'),
        ('Toys',    'Toys & Games'),
        ('Gifts',   'Gift Sets'),
    ]
    for old, new in renames:
        conn.execute(sa.text(
            "UPDATE products SET category = :new WHERE category = :old"
        ), {"new": new, "old": old})

    # 2. Ensure Caps sub-category exists under Bags & Pouches
    conn.execute(sa.text("""
        INSERT INTO categories (name, slug, parent_id, emoji, sort_order)
        SELECT 'Caps', 'caps',
               (SELECT id FROM categories WHERE slug = 'bags'),
               '🧢', 99
        WHERE NOT EXISTS (SELECT 1 FROM categories WHERE slug = 'caps')
    """))

    # 3. Re-home Other products
    other_updates = [
        # (names, category, sub_category_slug)
        (
            ('Camera watch', 'Digital cartoon watch', 'Light watch',
             'Mosquito repel watch', 'JBL'),
            'Toys & Games', 'watches-gadgets'
        ),
        (('Lego band',),      'Toys & Games',    'diy-creative'),
        (('Swim goggles',),   'Toys & Games',    'activity'),
        (('Mini needle set',),'Beauty & Hair',   'hygiene'),
        (('Specs case', 'Packing'), 'Bags & Pouches', 'bags'),
        (('Glass jar', 'Car fragrance'), 'Gift Sets', 'gift-sets'),
        (('Cap', 'Plain cap'), 'Bags & Pouches', 'caps'),
    ]
    for names, category, sub_slug in other_updates:
        placeholders = ', '.join(f':n{i}' for i in range(len(names)))
        params = {f'n{i}': name for i, name in enumerate(names)}
        params['category'] = category
        params['sub'] = sub_slug
        conn.execute(sa.text(
            f"UPDATE products "
            f"SET category = :category, sub_category_slug = :sub "
            f"WHERE category = 'Other' AND name IN ({placeholders})"
        ), params)

    # Set category_id for Cap / Plain cap now that Caps row exists
    conn.execute(sa.text("""
        UPDATE products
        SET category_id = (SELECT id FROM categories WHERE slug = 'caps')
        WHERE sub_category_slug = 'caps' AND category_id IS NULL
    """))

    # 4. Phone Charms → Keychains (category_id already correct)
    conn.execute(sa.text(
        "UPDATE products SET category = 'Keychains' WHERE category = 'Phone Charms'"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    # Reverse clean renames
    renames = [
        ('Bags & Pouches',  'Bags'),
        ('Beauty & Hair',   'Beauty'),
        ('Bottles & Lunch', 'Bottles'),
        ('Toys & Games',    'Toys'),
        ('Gift Sets',       'Gifts'),
    ]
    for new, old in renames:
        conn.execute(sa.text(
            "UPDATE products SET category = :old WHERE category = :new"
        ), {"new": new, "old": old})
    conn.execute(sa.text(
        "UPDATE products SET category = 'Phone Charms' WHERE category = 'Keychains' "
        "AND sub_category_slug = 'phone-charms'"
    ))
