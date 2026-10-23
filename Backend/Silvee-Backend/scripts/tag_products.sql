-- Auto-tagging script for recipients and occasions
-- Run inside a transaction — review the SELECT counts first, then COMMIT.
-- Safe to re-run: each rule guards with NOT (tag = ANY(recipients/occasions)).
--
-- Category map (from DB):
--   Bags, Bags & Pouches  → school bags, pencil pouches, lunch bags, duffle
--   Beauty, Beauty & Hair → bath/body, hair, hygiene, nails/jewellery
--   Bottles, Bottles & Lunch
--   Gifts, Gift Sets
--   Keychains, Phone Charms
--   Stationery            → art-craft, erasers, pens-pencils, sharpeners, sets
--   Toys, Toys & Games    → activity, board-games, diy-creative, puzzles, watches-gadgets
--   Other

BEGIN;

-- ══════════════════════════════════════════════════════════════════
--  RECIPIENTS
-- ══════════════════════════════════════════════════════════════════

-- little (ages 0–8): toys, stationery, bags
UPDATE products
SET recipients = array_append(recipients, 'little')
WHERE is_active = true
  AND NOT ('little' = ANY(recipients))
  AND (
    category ILIKE '%toy%'
    OR category ILIKE '%stationery%'
    OR sub_category_slug IN ('art-craft','diy-creative','activity','puzzles','board-games')
    OR category = 'Bags'
  );

-- school (ages 6–12): stationery, bags, bottles, school-type toys
UPDATE products
SET recipients = array_append(recipients, 'school')
WHERE is_active = true
  AND NOT ('school' = ANY(recipients))
  AND (
    category ILIKE '%stationery%'
    OR category IN ('Bags','Bags & Pouches','Bottles','Bottles & Lunch')
    OR sub_category_slug IN ('board-games','diy-creative','activity','puzzles',
                              'pencil-pouches','lunch-bags','bags')
  );

-- tween (ages 10–14): gadgets, keychains, phone charms, beauty/nails
UPDATE products
SET recipients = array_append(recipients, 'tween')
WHERE is_active = true
  AND NOT ('tween' = ANY(recipients))
  AND (
    sub_category_slug IN ('watches-gadgets','keychains','phone-charms','nails-jewellery')
    OR category IN ('Keychains','Phone Charms')
  );

-- her: beauty/hair, gift sets, bags, keychains, phone charms
UPDATE products
SET recipients = array_append(recipients, 'her')
WHERE is_active = true
  AND NOT ('her' = ANY(recipients))
  AND (
    category IN ('Beauty','Beauty & Hair','Gift Sets','Bags','Bags & Pouches',
                 'Keychains','Phone Charms','Gifts')
    OR sub_category_slug IN ('bath-body','hair','hygiene','nails-jewellery',
                              'gift-sets','trolley','keychains','phone-charms')
  );

-- him: board-games, watches-gadgets, keychains
UPDATE products
SET recipients = array_append(recipients, 'him')
WHERE is_active = true
  AND NOT ('him' = ANY(recipients))
  AND (
    sub_category_slug IN ('board-games','watches-gadgets','keychains')
    OR category = 'Keychains'
  );

-- ══════════════════════════════════════════════════════════════════
--  OCCASIONS
-- ══════════════════════════════════════════════════════════════════

-- school: stationery, bags, bottles — back-to-school buys
UPDATE products
SET occasions = array_append(occasions, 'school')
WHERE is_active = true
  AND NOT ('school' = ANY(occasions))
  AND (
    category ILIKE '%stationery%'
    OR category IN ('Bags','Bags & Pouches','Bottles','Bottles & Lunch')
    OR sub_category_slug IN ('pencil-pouches','lunch-bags','bags',
                              'erasers','sharpeners','pens-pencils','art-craft')
  );

-- return: small giveaways — keychains, erasers, pencils, phone charms
UPDATE products
SET occasions = array_append(occasions, 'return')
WHERE is_active = true
  AND NOT ('return' = ANY(occasions))
  AND (
    category IN ('Keychains','Phone Charms')
    OR sub_category_slug IN ('erasers','sharpeners','pens-pencils',
                              'keychains','phone-charms')
  );

-- birthday: toys, gift sets, gifts, bags, beauty, gadgets
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
  );

-- just: beauty, keychains, phone charms, gift sets
UPDATE products
SET occasions = array_append(occasions, 'just')
WHERE is_active = true
  AND NOT ('just' = ANY(occasions))
  AND (
    category IN ('Beauty','Beauty & Hair','Keychains','Phone Charms','Gift Sets','Gifts')
    OR sub_category_slug IN ('bath-body','hair','hygiene','nails-jewellery',
                              'keychains','phone-charms','gift-sets','trolley')
  );

-- ══════════════════════════════════════════════════════════════════
--  REVIEW — counts per tag before committing
-- ══════════════════════════════════════════════════════════════════
SELECT 'recipients' AS tag_type,
       unnest(recipients) AS tag,
       count(*) AS products
FROM products WHERE is_active = true
GROUP BY 1,2 ORDER BY 1,3 DESC;

SELECT 'occasions' AS tag_type,
       unnest(occasions) AS tag,
       count(*) AS products
FROM products WHERE is_active = true
GROUP BY 1,2 ORDER BY 1,3 DESC;

COMMIT;
