-- Step 14: Category taxonomy merge
-- Renames the legacy category strings and re-homes 14 "Other" products.
-- Safe to re-run: guard conditions ensure no double-updates.
-- Review the verification SELECTs before typing COMMIT.

BEGIN;

-- ══════════════════════════════════════════════════════════════════
-- 1.  Clean category renames  (162 products, text column only)
-- ══════════════════════════════════════════════════════════════════
UPDATE products SET category = 'Bags & Pouches'  WHERE category = 'Bags';
UPDATE products SET category = 'Beauty & Hair'   WHERE category = 'Beauty';
UPDATE products SET category = 'Bottles & Lunch' WHERE category = 'Bottles';
UPDATE products SET category = 'Toys & Games'    WHERE category = 'Toys';
UPDATE products SET category = 'Gift Sets'       WHERE category = 'Gifts';

-- ══════════════════════════════════════════════════════════════════
-- 2.  Re-home "Other" products
-- ══════════════════════════════════════════════════════════════════

-- Watches / gadgets
UPDATE products
SET category = 'Toys & Games', sub_category_slug = 'watches-gadgets'
WHERE category = 'Other'
  AND name IN ('Camera watch', 'Digital cartoon watch', 'Light watch',
               'Mosquito repel watch', 'JBL');

-- Lego band
UPDATE products
SET category = 'Toys & Games', sub_category_slug = 'diy-creative'
WHERE category = 'Other' AND name = 'Lego band';

-- Swim goggles
UPDATE products
SET category = 'Toys & Games', sub_category_slug = 'activity'
WHERE category = 'Other' AND name = 'Swim goggles';

-- Mini needle set
UPDATE products
SET category = 'Beauty & Hair', sub_category_slug = 'hygiene'
WHERE category = 'Other' AND name = 'Mini needle set';

-- Specs case + Packing
UPDATE products
SET category = 'Bags & Pouches', sub_category_slug = 'bags'
WHERE category = 'Other' AND name IN ('Specs case', 'Packing');

-- Glass jar + Car fragrance
UPDATE products
SET category = 'Gift Sets', sub_category_slug = 'gift-sets'
WHERE category = 'Other' AND name IN ('Glass jar', 'Car fragrance');

-- Cap + Plain cap — new "Caps" sub-category under Bags & Pouches
INSERT INTO categories (name, slug, parent_id, emoji, sort_order)
VALUES ('Caps', 'caps', '73931243-2e66-4ea3-a37d-1d6f9582426d', '🧢', 99)
ON CONFLICT (slug) DO NOTHING;

UPDATE products
SET category          = 'Bags & Pouches',
    sub_category_slug = 'caps',
    category_id       = (SELECT id FROM categories WHERE slug = 'caps')
WHERE category = 'Other' AND name IN ('Cap', 'Plain cap');

-- ══════════════════════════════════════════════════════════════════
-- 3.  Phone Charms → Keychains  (1 product, category_id stays)
-- ══════════════════════════════════════════════════════════════════
UPDATE products
SET category = 'Keychains'
WHERE category = 'Phone Charms';

-- ══════════════════════════════════════════════════════════════════
-- 4.  Verification — review before COMMIT
-- ══════════════════════════════════════════════════════════════════

-- Active product counts per category after migration:
SELECT category, count(*) AS products
FROM products
WHERE is_active = true
GROUP BY 1
ORDER BY 1;

-- Should return 0 rows (no legacy names remain):
SELECT name, category
FROM products
WHERE is_active = true
  AND category IN ('Bags','Beauty','Bottles','Toys','Gifts','Other','Phone Charms');

COMMIT;
