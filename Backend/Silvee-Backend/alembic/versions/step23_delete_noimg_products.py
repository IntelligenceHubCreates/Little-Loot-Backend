"""Step 23: permanently delete 85 products without photos (owner's checklist, 02 Nov 2026)

Revision ID: step23_delete_noimg_products
Revises: step22_bulk_enquiries
Create Date: 2026-11-02

The owner ticked the products to KEEP on the "Products without photos"
checklist; these are the 85 unticked ones. For each product:

  1. A full copy of the row is saved to products_deleted_backup (JSON) first,
     so a mistake can be undone. Drop that table once you're sure.
  2. If it appears in a real order / return / shipment, deleting it would break
     those financial records (and FKs forbid it), so it is only hidden
     (is_active = false) instead.
  3. Otherwise its cart lines, wishlist entries and reviews are removed, it is
     dropped from other products' pairs_with, and the product row is deleted.
     Bulk enquiries keep their snapshot (product_id is set NULL by the FK).

Idempotent: ids that no longer exist are skipped.
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = 'step23_delete_noimg_products'   # alembic_version.version_num is VARCHAR(32)
down_revision: Union[str, None] = 'step22_bulk_enquiries'
branch_labels = None
depends_on = None

# (product id, product code) — the 85 unticked products
PRODUCTS = [
    ('3107d9ed-d65c-492d-a37d-057828422005', 'LL-BAG-0002'),  # Bento lunch bags
    ('4f410bbc-2047-4bde-85f5-053b74b5154f', 'LL-BAG-0005'),  # Cute mini duffle
    ('79a4e704-13e2-4e66-a925-d801f671a62f', 'LL-BAG-0009'),  # Jelly duffle bag
    ('0fc0e6f4-d7ac-4160-ab53-7dbd7398fa8c', 'LL-BAG-0010'),  # Jelly makeup pouch
    ('aa9eed21-1ee8-4de5-9738-cff66fc591de', 'LL-BAG-0015'),  # Packing
    ('3f0df2cd-3f93-4993-b476-d11ce0786119', 'LL-BAG-0020'),  # Silicone totes
    ('716b2025-70f5-45e4-91ee-a76e1cb55630', 'LL-BAG-0030'),  # Black bling
    ('4075abc4-8a82-4d10-b695-e33b27afef21', 'LL-BAG-0036'),  # Double pouch
    ('52b5544c-114b-40e4-880a-fcba79e55d21', 'LL-BAG-0050'),  # School Bags R
    ('640d50f3-f441-46fe-b9b2-ada5e3dcc0f2', 'LL-BTL-0001'),  # Chaco luxury bottle
    ('72160cbf-6b57-47a0-a279-7bd19bd82591', 'LL-BTL-0003'),  # Character small mug
    ('2bf19225-1fdf-4dc5-8e46-871f8100f130', 'LL-BTL-0009'),  # No handle Stanley cups
    ('4cc7724e-b6d1-4c04-9321-6eb534650a3f', 'LL-BTL-0015'),  # 470ml football bottle
    ('56c4d8da-2fb6-45da-80e0-747b6264f5be', 'LL-BTL-0019'),  # 800ml sports bottle
    ('20456db4-07a3-4241-864e-f449e3934760', 'LL-BTY-0001'),  # Bath salts
    ('462f58d4-edf1-4971-8d01-0dff68aa45a4', 'LL-BTY-0002'),  # Box hair set
    ('bd0248db-3443-4bbc-97a5-c98d8954e8b0', 'LL-BTY-0003'),  # Bracelet kids
    ('359b6d75-44d7-4add-8d70-5112d6c318f1', 'LL-BTY-0004'),  # Comb mirror set
    ('1b068527-8db4-4097-aa11-9d7bb02f8e73', 'LL-BTY-0005'),  # Compact
    ('ebb8e5c2-054a-4b57-8175-35bb0f307a16', 'LL-BTY-0006'),  # Ear muffs boys
    ('c1786d3d-5b8a-43ec-849a-949446e5f0ad', 'LL-BTY-0007'),  # Face cleanser
    ('a42d368c-1072-4abb-9b48-2ac9f97144c4', 'LL-BTY-0008'),  # Face palette
    ('1fbfb900-411d-4fad-bf31-41888089a44b', 'LL-BTY-0009'),  # Flower claw small
    ('0824db1f-7724-41a7-93c7-aec818049e41', 'LL-BTY-0012'),  # Hair dye
    ('4c96625f-4f30-4f7c-b8c9-9a03c9882c55', 'LL-BTY-0013'),  # Head bands
    ('e0b03cc1-3cf9-4b0a-a2bb-f46e20806e4d', 'LL-BTY-0014'),  # Homemade Soap
    ('6cf6622e-31b9-4743-9463-cce306eb369a', 'LL-BTY-0015'),  # Kids toothbrush
    ('00d6a5a7-133c-4b00-af2e-466a0876698c', 'LL-BTY-0018'),  # Moisturiser salve
    ('08a328e7-394f-4e59-b6da-45f52ff0d9af', 'LL-BTY-0019'),  # Shampoo bar
    ('202fc38e-2b41-44d0-9ff8-88cac6c4c969', 'LL-BTY-0020'),  # Shiny claw clip
    ('34e4f9e6-916a-4664-b80b-87577be9c642', 'LL-BTY-0022'),  # Sunscreen
    ('125b11bc-dc6f-40db-9a68-d578051bbb01', 'LL-BTY-0023'),  # Toothbrush
    ('6a9c56c1-4e30-4198-a1b2-89524a0af15a', 'LL-BTY-0024'),  # Baby hair pins R
    ('bb78a430-cb6f-482f-a9d8-6de6373d47cd', 'LL-BTY-0025'),  # Big hair set
    ('b86ce5b5-65ef-43fb-9bf9-6e7ec278e12b', 'LL-BTY-0026'),  # Bracelet
    ('796fa7f4-916d-4fae-961c-30c189cc99ed', 'LL-BTY-0027'),  # Bunny ear muffs
    ('50ffbfd4-2fd6-4ca0-b4cf-54af2741a0d6', 'LL-BTY-0028'),  # Bunny small pins
    ('22de564b-5311-46c0-ac84-0a76b2c56b5a', 'LL-BTY-0029'),  # Cartoon hair bands
    ('5454c6b1-38d1-443e-9f62-661e1ae5ac52', 'LL-BTY-0030'),  # DIY crown makeup
    ('7be69f24-48f8-478c-98a9-d2e762247f03', 'LL-BTY-0031'),  # Flower claw big
    ('d210ae4a-43aa-4d34-a9dd-71254bbddfab', 'LL-BTY-0034'),  # Hair oil
    ('65bf6640-102b-4f93-898a-e06cc55d35ad', 'LL-BTY-0038'),  # Shampoo powder
    ('708e1074-a4f2-40ad-9565-2d2d6854ce16', 'LL-BTY-0039'),  # Small hair set
    ('85e9d805-a0ac-41fd-ac48-e136f8bfc587', 'LL-GFT-0001'),  # Box accessories set
    ('a123eeb6-35e6-471f-bf4a-5de436599209', 'LL-GFT-0003'),  # Gift set sha
    ('58b5e50a-df64-43b6-b3bd-b33d2c763f23', 'LL-GFT-0007'),  # Big box accessory set
    ('2f7209b9-2dae-4482-b0f5-6d0551f1c924', 'LL-GFT-0008'),  # Car fragrance
    ('8bde391e-3ee4-4f42-8e93-f503432c2e38', 'LL-KEY-0004'),  # Mobile keychain 78
    ('54a459d8-9c0e-4579-ba5a-bf4b4eddf05a', 'LL-STA-0001'),  # 2 in 1 sharpener
    ('17b1e41c-101c-4746-9cff-f773d4b732ae', 'LL-STA-0002'),  # 6685
    ('8b8bcfed-65e2-43d6-b6d6-e7336181aade', 'LL-STA-0004'),  # 6pencil set
    ('9331f6e8-4cb5-428e-acda-38dcaac03813', 'LL-STA-0007'),  # Alphabets
    ('2f0bddb6-c428-45f7-a110-5daa3bf84f72', 'LL-STA-0010'),  # Blister 905
    ('6172fdf8-170b-47c2-9f0c-8c8c9499e1b7', 'LL-STA-0012'),  # Car sharpener
    ('04652482-4214-492b-a7a9-bf9d4c83a8b4', 'LL-STA-0013'),  # Character add ons
    ('acd68e8e-db97-43e3-b15f-640bc5e95e79', 'LL-STA-0014'),  # Character pencils
    ('626c02b1-30b3-4604-b6a0-95a6e9a1c813', 'LL-STA-0020'),  # Doodle scale kit
    ('1c509e31-4b02-464c-9662-cbd674e7f0e0', 'LL-STA-0026'),  # Kromi scale
    ('5e5d5800-7900-4954-863d-9baa4933e333', 'LL-STA-0027'),  # Kromi sharpeners
    ('781aae9e-b188-46d6-ab21-5aa560a5b4a3', 'LL-STA-0028'),  # Massage pen
    ('5f23a1f3-d313-4def-a853-de657f3f204c', 'LL-STA-0031'),  # Pencil kromi
    ('17d708a9-47e9-4199-b796-08e943c7549f', 'LL-STA-0033'),  # Phone sharpener
    ('0edff445-22ea-42b0-a545-5d01b8e5ee1c', 'LL-STA-0037'),  # Stamp with highlighter
    ('9d0040b8-01b2-462a-b3da-63d33b8d59ce', 'LL-STA-0041'),  # 2250
    ('dc584d65-1387-4743-95a3-e16245566155', 'LL-STA-0042'),  # 3d diary
    ('0c8b1064-98f2-41d5-bfd1-8bd5b9854c5e', 'LL-STA-0045'),  # Bear sharpener
    ('011c6fdf-d405-4bba-9bdc-6a8756934eb1', 'LL-STA-0047'),  # Bow eraser
    ('00e374a2-6576-410e-8ac0-7941226b6a47', 'LL-STA-0050'),  # Car eraser
    ('3f1999b8-0fd0-4c3d-93d8-cd90e92f24d1', 'LL-STA-0052'),  # Character patche
    ('68e00d70-1091-4d9a-942f-e8e910bda0c3', 'LL-STA-0055'),  # Clip kromi book
    ('0bcde1a8-d077-4bb7-91f7-a0c3b5108df4', 'LL-STA-0058'),  # Dino sharpener
    ('5c2d5627-8cf7-4c35-b02d-2af932b5e956', 'LL-STA-0059'),  # Dizzy eraser
    ('77bf2960-6283-4a15-8c9f-fde897f3c0f2', 'LL-STA-0062'),  # Extrenal pencil
    ('53139493-f9b3-419e-92df-1d5fb87e09cc', 'LL-STA-0066'),  # Highlighter
    ('5ec33b0e-84b8-41ed-8d43-2e0bd484b9a7', 'LL-STA-0069'),  # Kawai eraser
    ('3fe18da1-2a78-43c5-8497-2f1511ff9c36', 'LL-STA-0070'),  # Kromi eraser
    ('30ada876-9569-494f-95c7-507185d49cc6', 'LL-STA-0071'),  # Kuromi stationary set
    ('64353db1-836e-497e-b6dd-f7100f098403', 'LL-STA-0074'),  # Mini 4 color pens
    ('1912688f-985b-4f69-a064-36df40f6e839', 'LL-STA-0080'),  # Sneaker eraser
    ('be5d769b-d673-41a5-a1b1-45338377c76b', 'LL-STA-0083'),  # Stamps
    ('315b19db-b11c-449f-a99f-2c739a3b3ec0', 'LL-TOY-0004'),  # Moon ball
    ('7f26e0f1-fcf0-4f75-be72-8640f477fd54', 'LL-TOY-0008'),  # Spring toy
    ('5b823d1e-4c59-400c-b9f4-18d16ecbb58d', 'LL-TOY-0013'),  # Digital cartoon watch
    ('00f54a05-9a1c-4cd1-b13c-a4ebc627b602', 'LL-TOY-0015'),  # Piggy bank R big
    ('16d66eb4-8263-4038-976c-cf8dd2fc9d26', 'LL-TOY-0017'),  # Piggy banks
]


def upgrade() -> None:
    values = ", ".join(f"('{pid}'::uuid, '{sku}')" for pid, sku in PRODUCTS)
    op.execute(sa.text(f"""
        CREATE TABLE IF NOT EXISTS products_deleted_backup (
            id          UUID PRIMARY KEY,
            sku         VARCHAR(20),
            row_data    JSONB NOT NULL,
            deleted_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TEMP TABLE _targets ON COMMIT DROP AS
        SELECT v.id, v.sku FROM (VALUES {values}) AS v(id, sku)
        WHERE EXISTS (SELECT 1 FROM products p WHERE p.id = v.id);

        -- Products with sales / return / shipping history: hide only
        CREATE TEMP TABLE _keep_hidden ON COMMIT DROP AS
        SELECT t.id FROM _targets t
        WHERE EXISTS (SELECT 1 FROM order_items           x WHERE x.product_id = t.id)
           OR EXISTS (SELECT 1 FROM return_items          x WHERE x.product_id = t.id)
           OR EXISTS (SELECT 1 FROM replacement_shipments x WHERE x.product_id = t.id)
           OR EXISTS (SELECT 1 FROM shipment_items        x WHERE x.product_id = t.id);

        UPDATE products SET is_active = false WHERE id IN (SELECT id FROM _keep_hidden);
        DELETE FROM _targets WHERE id IN (SELECT id FROM _keep_hidden);

        -- Backup, then remove dependents and the products
        INSERT INTO products_deleted_backup (id, sku, row_data)
        SELECT p.id, p.sku, to_jsonb(p) FROM products p WHERE p.id IN (SELECT id FROM _targets)
        ON CONFLICT (id) DO NOTHING;

        DELETE FROM cart_items WHERE product_id IN (SELECT id FROM _targets);
        DELETE FROM favorites  WHERE product_id IN (SELECT id FROM _targets);
        DELETE FROM ratings    WHERE product_id IN (SELECT id FROM _targets);

        UPDATE products p
        SET pairs_with = ARRAY(SELECT x FROM unnest(p.pairs_with) AS x WHERE x NOT IN (SELECT id FROM _targets))
        WHERE p.pairs_with && ARRAY(SELECT id FROM _targets);

        DELETE FROM products WHERE id IN (SELECT id FROM _targets);
    """))


def downgrade() -> None:
    # Restore deleted rows from the backup (dependents like cart lines are not restored)
    op.execute(sa.text("""
        INSERT INTO products SELECT (jsonb_populate_record(NULL::products, b.row_data)).*
        FROM products_deleted_backup b
        WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.id = b.id);
    """))
