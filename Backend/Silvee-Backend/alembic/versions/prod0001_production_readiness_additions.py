"""Production readiness — new tables and missing columns

Revision ID: prod0001
Revises: 6707115b61f9
Create Date: 2026-07-11

This migration is intentionally idempotent.  Every statement is guarded by
IF NOT EXISTS (for tables / indexes) or a WHEN duplicate_column / duplicate_object
PL/pgSQL exception block (for ALTER TABLE ADD COLUMN).  It therefore runs safely:
  - on a fresh RDS where only Alembic migrations have run (normal production path)
  - on a dev DB that previously had create_all() populate the schema

Nothing in this migration drops, truncates, or destructively alters existing data.

Summary of additions
---------------------
NEW TABLES (16):
  categories, payment_orders, password_reset_tokens, blog_posts, coupons,
  store_settings, courier_partners, shipments, shipment_items,
  shipment_status_history, delivery_attempts, shipping_labels,
  return_requests, return_items, return_proofs, return_status_history,
  refunds, replacement_shipments, notifications, shipment_notification_logs

NEW COLUMNS on existing tables:
  users              → is_active, profile_picture, dob, gender
  user_addresses     → full_name, phone, address_type
  orders             → razorpay_payment_id, delivered_at, subtotal,
                       discount_amount, delivery_fee, coupon_code, gift_message
  order_items        → color, color_hex, image
  cart_items         → color, color_hex, image
  products           → category_id (FK), description, count, brand, age_range,
                       is_new, is_featured, is_active, created_at, updated_at,
                       variant_group_id, color, color_hex, color_variants,
                       product_video, sub_category_slug, sub_category_name
  ratings            → order_id, comment, helpful_count, is_approved, created_at

PG FUNCTION:
  get_category_ids(slug TEXT) — recursive CTE for category tree look-ups
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'prod0001'
down_revision: Union[str, None] = '6707115b61f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ─── helpers ──────────────────────────────────────────────────────────────────

def _add_col(table: str, col_sql: str) -> None:
    """ADD COLUMN if it does not already exist (idempotent)."""
    op.execute(sa.text(f"""
        DO $$ BEGIN
            ALTER TABLE {table} ADD COLUMN {col_sql};
        EXCEPTION
            WHEN duplicate_column THEN NULL;
        END $$;
    """))


def _create_index_if_not_exists(name: str, table: str, col: str) -> None:
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({col});"
    ))


# ─── upgrade ──────────────────────────────────────────────────────────────────

def upgrade() -> None:

    # ── 1. categories ─────────────────────────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS categories (
            id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            name        VARCHAR(120) NOT NULL,
            slug        VARCHAR(120) NOT NULL UNIQUE,
            parent_id   UUID        REFERENCES categories(id) ON DELETE SET NULL,
            emoji       VARCHAR(10),
            description TEXT,
            sort_order  INTEGER     NOT NULL DEFAULT 0,
            is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """))
    _create_index_if_not_exists('ix_categories_slug', 'categories', 'slug')

    # ── 2. products — add FK and new columns ──────────────────────────────────
    # category_id FK must be added after categories table exists
    _add_col('products', 'category_id UUID REFERENCES categories(id) ON DELETE SET NULL')
    _create_index_if_not_exists('ix_products_category_id', 'products', 'category_id')
    _add_col('products', 'description TEXT')
    _add_col('products', 'count INTEGER NOT NULL DEFAULT 0')
    _add_col('products', 'brand VARCHAR(120)')
    _add_col('products', 'age_range VARCHAR(40)')
    _add_col('products', 'is_new BOOLEAN NOT NULL DEFAULT FALSE')
    _add_col('products', 'is_featured BOOLEAN NOT NULL DEFAULT FALSE')
    _add_col('products', 'is_active BOOLEAN NOT NULL DEFAULT TRUE')
    _add_col('products', 'created_at TIMESTAMPTZ DEFAULT now()')
    _add_col('products', 'updated_at TIMESTAMPTZ DEFAULT now()')
    _add_col('products', 'variant_group_id VARCHAR(64)')
    _add_col('products', 'color VARCHAR(50)')
    _add_col('products', 'color_hex VARCHAR(7)')
    _add_col('products', "color_variants JSON NOT NULL DEFAULT '[]'::json")
    _add_col('products', 'product_video VARCHAR(512)')
    _add_col('products', 'sub_category_slug VARCHAR(120)')
    _add_col('products', 'sub_category_name VARCHAR(120)')
    _create_index_if_not_exists('ix_products_variant_group_id', 'products', 'variant_group_id')
    _create_index_if_not_exists('ix_products_sub_category_slug', 'products', 'sub_category_slug')

    # ── 3. users — new columns ────────────────────────────────────────────────
    _add_col('users', 'is_active BOOLEAN NOT NULL DEFAULT TRUE')
    _add_col('users', 'profile_picture VARCHAR(500)')
    _add_col('users', 'dob VARCHAR(20)')
    _add_col('users', 'gender VARCHAR(20)')

    # ── 4. user_addresses — new columns ───────────────────────────────────────
    _add_col("user_addresses", "full_name VARCHAR(255) NOT NULL DEFAULT ''")
    _add_col("user_addresses", "phone VARCHAR(20) NOT NULL DEFAULT ''")
    _add_col("user_addresses", "address_type VARCHAR(20) NOT NULL DEFAULT 'home'")

    # ── 5. orders — new columns ───────────────────────────────────────────────
    _add_col('orders', 'razorpay_payment_id VARCHAR(100)')
    _add_col('orders', 'delivered_at TIMESTAMPTZ')
    _add_col('orders', 'subtotal DECIMAL(20,2)')
    _add_col('orders', 'discount_amount DECIMAL(20,2) DEFAULT 0')
    _add_col('orders', 'delivery_fee DECIMAL(20,2) DEFAULT 0')
    _add_col('orders', 'coupon_code VARCHAR(50)')
    _add_col('orders', 'gift_message VARCHAR(500)')
    _create_index_if_not_exists('ix_orders_razorpay_payment_id', 'orders', 'razorpay_payment_id')

    # ── 6. order_items — new columns ──────────────────────────────────────────
    _add_col('order_items', 'color VARCHAR(50)')
    _add_col('order_items', 'color_hex VARCHAR(7)')
    _add_col('order_items', 'image TEXT')

    # ── 7. cart_items — new columns ───────────────────────────────────────────
    _add_col('cart_items', 'color VARCHAR(50)')
    _add_col('cart_items', 'color_hex VARCHAR(7)')
    _add_col('cart_items', 'image TEXT')

    # ── 8. ratings — new columns ──────────────────────────────────────────────
    _add_col('ratings', 'order_id UUID REFERENCES orders(id)')
    _add_col('ratings', "comment TEXT NOT NULL DEFAULT ''")
    _add_col('ratings', 'helpful_count INTEGER NOT NULL DEFAULT 0')
    _add_col('ratings', 'is_approved BOOLEAN NOT NULL DEFAULT TRUE')
    _add_col('ratings', 'created_at TIMESTAMPTZ NOT NULL DEFAULT now()')

    # ── 9. payment_orders ─────────────────────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS payment_orders (
            id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id             UUID        REFERENCES users(id) ON DELETE SET NULL,
            razorpay_order_id   VARCHAR(100) NOT NULL UNIQUE,
            razorpay_payment_id VARCHAR(100),
            razorpay_signature  VARCHAR(300),
            amount              INTEGER     NOT NULL,
            currency            VARCHAR(10) DEFAULT 'INR',
            status              VARCHAR(30) DEFAULT 'created',
            cart_snapshot       JSON,
            shipping_address    JSON,
            is_verified         BOOLEAN     DEFAULT FALSE,
            created_at          TIMESTAMPTZ DEFAULT now(),
            paid_at             TIMESTAMPTZ
        );
    """))
    _create_index_if_not_exists('ix_payment_orders_razorpay_order_id', 'payment_orders', 'razorpay_order_id')
    _create_index_if_not_exists('ix_payment_orders_razorpay_payment_id', 'payment_orders', 'razorpay_payment_id')

    # ── 10. password_reset_tokens ─────────────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash  VARCHAR(64) NOT NULL UNIQUE,
            expires_at  TIMESTAMPTZ NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """))
    _create_index_if_not_exists('ix_password_reset_tokens_token_hash', 'password_reset_tokens', 'token_hash')

    # ── 11. blog_posts ────────────────────────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS blog_posts (
            id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            title      VARCHAR(255) NOT NULL,
            slug       VARCHAR(255) NOT NULL UNIQUE,
            excerpt    TEXT         DEFAULT '',
            content    TEXT         DEFAULT '',
            tag        VARCHAR(80)  DEFAULT '',
            image_url  VARCHAR(600),
            status     VARCHAR(20)  NOT NULL DEFAULT 'draft',
            views      INTEGER      NOT NULL DEFAULT 0,
            comments   INTEGER      NOT NULL DEFAULT 0,
            likes      INTEGER      NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ  DEFAULT now(),
            updated_at TIMESTAMPTZ  DEFAULT now()
        );
    """))
    _create_index_if_not_exists('ix_blog_posts_slug', 'blog_posts', 'slug')

    # ── 12. coupons ───────────────────────────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS coupons (
            id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            code           VARCHAR(50) NOT NULL UNIQUE,
            discount_type  VARCHAR(20) NOT NULL,
            discount_value FLOAT       NOT NULL,
            min_order      FLOAT       DEFAULT 0,
            max_uses       INTEGER     DEFAULT 100,
            used_count     INTEGER     DEFAULT 0,
            is_active      BOOLEAN     DEFAULT TRUE,
            expires_at     TIMESTAMPTZ,
            created_at     TIMESTAMPTZ DEFAULT now()
        );
    """))
    _create_index_if_not_exists('ix_coupons_code', 'coupons', 'code')

    # ── 13. store_settings ────────────────────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS store_settings (
            key        VARCHAR(80) PRIMARY KEY,
            value      TEXT,
            updated_at TIMESTAMPTZ DEFAULT now()
        );
    """))

    # ── 14. returns cluster ───────────────────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS return_requests (
            id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            order_id            UUID        NOT NULL REFERENCES orders(id),
            user_id             UUID        NOT NULL REFERENCES users(id),
            status              VARCHAR(40) NOT NULL DEFAULT 'requested',
            request_type        VARCHAR(20) NOT NULL,
            reason              VARCHAR(30) NOT NULL,
            description         TEXT,
            total_refund_amount DECIMAL(20,2),
            admin_notes         TEXT,
            rejection_reason    TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """))
    _create_index_if_not_exists('ix_return_requests_order_id', 'return_requests', 'order_id')
    _create_index_if_not_exists('ix_return_requests_user_id',  'return_requests', 'user_id')
    _create_index_if_not_exists('ix_return_requests_status',   'return_requests', 'status')

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS return_items (
            id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            return_request_id UUID        NOT NULL REFERENCES return_requests(id) ON DELETE CASCADE,
            order_item_id     UUID        NOT NULL REFERENCES order_items(id),
            product_id        UUID        NOT NULL REFERENCES products(id),
            quantity          INTEGER     NOT NULL,
            item_price        DECIMAL(20,2) NOT NULL,
            condition_status  VARCHAR(20) NOT NULL DEFAULT 'pending',
            restock_quantity  INTEGER     NOT NULL DEFAULT 0,
            is_resellable     BOOLEAN     NOT NULL DEFAULT FALSE
        );
    """))
    _create_index_if_not_exists('ix_return_items_return_request_id', 'return_items', 'return_request_id')

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS return_proofs (
            id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            return_request_id UUID        NOT NULL REFERENCES return_requests(id) ON DELETE CASCADE,
            file_url          TEXT        NOT NULL,
            file_type         VARCHAR(20) NOT NULL DEFAULT 'image',
            public_id         VARCHAR(255),
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """))
    _create_index_if_not_exists('ix_return_proofs_return_request_id', 'return_proofs', 'return_request_id')

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS return_status_history (
            id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            return_request_id   UUID        NOT NULL REFERENCES return_requests(id) ON DELETE CASCADE,
            old_status          VARCHAR(40),
            new_status          VARCHAR(40) NOT NULL,
            changed_by_admin_id UUID        REFERENCES users(id) ON DELETE SET NULL,
            note                TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """))
    _create_index_if_not_exists('ix_return_status_history_return_request_id', 'return_status_history', 'return_request_id')

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS refunds (
            id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            return_request_id     UUID        NOT NULL UNIQUE REFERENCES return_requests(id) ON DELETE CASCADE,
            amount                DECIMAL(20,2) NOT NULL,
            method                VARCHAR(30) NOT NULL DEFAULT 'manual',
            status                VARCHAR(20) NOT NULL DEFAULT 'pending',
            transaction_reference VARCHAR(255),
            processed_at          TIMESTAMPTZ,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            gateway_refund_id     VARCHAR(64),
            gateway_payment_id    VARCHAR(64),
            gateway_status        VARCHAR(20),
            speed                 VARCHAR(12)
        );
    """))
    _create_index_if_not_exists('ix_refunds_return_request_id',  'refunds', 'return_request_id')
    _create_index_if_not_exists('ix_refunds_gateway_refund_id',  'refunds', 'gateway_refund_id')

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS replacement_shipments (
            id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            return_request_id UUID        NOT NULL UNIQUE REFERENCES return_requests(id) ON DELETE CASCADE,
            product_id        UUID        NOT NULL REFERENCES products(id),
            quantity          INTEGER     NOT NULL DEFAULT 1,
            status            VARCHAR(20) NOT NULL DEFAULT 'pending',
            tracking_number   VARCHAR(120),
            dispatched_at     TIMESTAMPTZ,
            delivered_at      TIMESTAMPTZ,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """))
    _create_index_if_not_exists('ix_replacement_shipments_return_request_id', 'replacement_shipments', 'return_request_id')

    # ── 15. courier_partners (must precede shipments FK) ──────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS courier_partners (
            id                    UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            name                  VARCHAR(120) NOT NULL,
            service_type          VARCHAR(120),
            is_active             BOOLEAN      NOT NULL DEFAULT TRUE,
            supports_cod          BOOLEAN      NOT NULL DEFAULT TRUE,
            tracking_url_template TEXT,
            created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
    """))

    # ── 16. shipments cluster ─────────────────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS shipments (
            id                       UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            order_id                 UUID         NOT NULL REFERENCES orders(id),
            user_id                  UUID         REFERENCES users(id),
            status                   VARCHAR(40)  NOT NULL DEFAULT 'pending',
            is_prepaid               BOOLEAN      NOT NULL DEFAULT FALSE,
            cod_amount               DECIMAL(20,2),
            cod_collected            BOOLEAN      NOT NULL DEFAULT FALSE,
            cod_collected_at         TIMESTAMPTZ,
            cod_remitted             BOOLEAN      NOT NULL DEFAULT FALSE,
            cod_remittance_reference VARCHAR(120),
            cod_remitted_at          TIMESTAMPTZ,
            courier_partner_id       UUID         REFERENCES courier_partners(id),
            courier_name             VARCHAR(120),
            courier_service          VARCHAR(120),
            awb_number               VARCHAR(120),
            tracking_url             TEXT,
            label_url                TEXT,
            label_public_id          VARCHAR(200),
            label_generated_at       TIMESTAMPTZ,
            shipping_cost            DECIMAL(20,2),
            package_weight           DECIMAL(20,2),
            package_length           DECIMAL(20,2),
            package_width            DECIMAL(20,2),
            package_height           DECIMAL(20,2),
            ship_name                VARCHAR(200),
            ship_phone               VARCHAR(20),
            ship_line1               VARCHAR(300),
            ship_city                VARCHAR(120),
            ship_state               VARCHAR(120),
            ship_pincode             VARCHAR(12),
            expected_delivery_date   DATE,
            pickup_scheduled_at      TIMESTAMPTZ,
            pickup_attempts          INTEGER      NOT NULL DEFAULT 0,
            packed_at                TIMESTAMPTZ,
            picked_up_at             TIMESTAMPTZ,
            delivered_at             TIMESTAMPTZ,
            rto_initiated_at         TIMESTAMPTZ,
            rto_received_at          TIMESTAMPTZ,
            admin_notes              TEXT,
            created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at               TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
    """))
    _create_index_if_not_exists('ix_shipments_order_id',    'shipments', 'order_id')
    _create_index_if_not_exists('ix_shipments_user_id',     'shipments', 'user_id')
    _create_index_if_not_exists('ix_shipments_status',      'shipments', 'status')
    _create_index_if_not_exists('ix_shipments_awb_number',  'shipments', 'awb_number')
    _create_index_if_not_exists('ix_shipments_ship_city',   'shipments', 'ship_city')
    _create_index_if_not_exists('ix_shipments_ship_state',  'shipments', 'ship_state')
    _create_index_if_not_exists('ix_shipments_ship_pincode','shipments', 'ship_pincode')

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS shipment_items (
            id               UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
            shipment_id      UUID    NOT NULL REFERENCES shipments(id),
            order_item_id    UUID    NOT NULL REFERENCES order_items(id),
            product_id       UUID    NOT NULL REFERENCES products(id),
            quantity         INTEGER NOT NULL,
            condition_status VARCHAR(20) NOT NULL DEFAULT 'pending',
            restock_quantity INTEGER NOT NULL DEFAULT 0,
            is_resellable    BOOLEAN NOT NULL DEFAULT FALSE
        );
    """))
    _create_index_if_not_exists('ix_shipment_items_shipment_id', 'shipment_items', 'shipment_id')

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS shipment_status_history (
            id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            shipment_id         UUID        NOT NULL REFERENCES shipments(id),
            old_status          VARCHAR(40),
            new_status          VARCHAR(40) NOT NULL,
            changed_by_admin_id UUID        REFERENCES users(id),
            note                TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """))
    _create_index_if_not_exists('ix_shipment_status_history_shipment_id', 'shipment_status_history', 'shipment_id')

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS delivery_attempts (
            id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            shipment_id        UUID        NOT NULL REFERENCES shipments(id),
            attempt_number     INTEGER     NOT NULL DEFAULT 1,
            attempted_at       TIMESTAMPTZ,
            status             VARCHAR(40) NOT NULL,
            failure_reason     VARCHAR(60),
            courier_remarks    TEXT,
            next_attempt_at    TIMESTAMPTZ,
            customer_contacted BOOLEAN     NOT NULL DEFAULT FALSE,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """))
    _create_index_if_not_exists('ix_delivery_attempts_shipment_id', 'delivery_attempts', 'shipment_id')

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS shipping_labels (
            id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            shipment_id     UUID         NOT NULL REFERENCES shipments(id),
            label_url       TEXT         NOT NULL,
            label_public_id VARCHAR(200),
            file_name       VARCHAR(200),
            generated_by    VARCHAR(40),
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
    """))
    _create_index_if_not_exists('ix_shipping_labels_shipment_id', 'shipping_labels', 'shipment_id')

    # ── 17. notifications ─────────────────────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS notifications (
            id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id    UUID         NOT NULL REFERENCES users(id),
            type       VARCHAR(40)  NOT NULL DEFAULT 'system',
            title      VARCHAR(200) NOT NULL,
            body       TEXT,
            link       TEXT,
            meta       JSONB,
            is_read    BOOLEAN      NOT NULL DEFAULT FALSE,
            read_at    TIMESTAMPTZ,
            created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
    """))
    _create_index_if_not_exists('ix_notifications_user_id',    'notifications', 'user_id')
    _create_index_if_not_exists('ix_notifications_type',       'notifications', 'type')
    _create_index_if_not_exists('ix_notifications_is_read',    'notifications', 'is_read')
    _create_index_if_not_exists('ix_notifications_created_at', 'notifications', 'created_at')

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS shipment_notification_logs (
            id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            shipment_id  UUID         REFERENCES shipments(id),
            user_id      UUID         REFERENCES users(id),
            event        VARCHAR(60)  NOT NULL,
            channel      VARCHAR(20)  NOT NULL,
            status       VARCHAR(30)  NOT NULL DEFAULT 'logged',
            provider     VARCHAR(40),
            provider_ref VARCHAR(200),
            error        TEXT,
            payload      JSONB,
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
    """))
    _create_index_if_not_exists('ix_shipment_notification_logs_shipment_id', 'shipment_notification_logs', 'shipment_id')

    # ── 18. get_category_ids() PostgreSQL function ────────────────────────────
    # Recursive CTE that returns the root category and all descendants by slug.
    # Used by Category.get_descendant_ids() for efficient sub-category filtering.
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION get_category_ids(p_slug TEXT)
        RETURNS TABLE(category_id UUID) AS $$
        WITH RECURSIVE tree AS (
            SELECT id FROM categories WHERE slug = p_slug
            UNION ALL
            SELECT c.id FROM categories c
            JOIN tree t ON c.parent_id = t.id
        )
        SELECT id AS category_id FROM tree;
        $$ LANGUAGE SQL STABLE;
    """))


# ─── downgrade ────────────────────────────────────────────────────────────────

def downgrade() -> None:
    # Reverse order to respect FK dependencies.
    op.execute(sa.text("DROP FUNCTION IF EXISTS get_category_ids(TEXT);"))

    for tbl in [
        'shipment_notification_logs', 'notifications',
        'shipping_labels', 'delivery_attempts', 'shipment_status_history',
        'shipment_items', 'shipments', 'courier_partners',
        'replacement_shipments', 'refunds', 'return_status_history',
        'return_proofs', 'return_items', 'return_requests',
        'store_settings', 'coupons', 'blog_posts',
        'password_reset_tokens', 'payment_orders',
    ]:
        op.execute(sa.text(f"DROP TABLE IF EXISTS {tbl} CASCADE;"))

    # products: drop new columns
    for col in ['sub_category_name', 'sub_category_slug', 'product_video',
                'color_variants', 'color_hex', 'color', 'variant_group_id',
                'updated_at', 'created_at', 'is_active', 'is_featured',
                'is_new', 'age_range', 'brand', 'count', 'description', 'category_id']:
        op.execute(sa.text(f"ALTER TABLE products DROP COLUMN IF EXISTS {col};"))

    # other table new columns
    for col in ['is_active', 'profile_picture', 'dob', 'gender']:
        op.execute(sa.text(f"ALTER TABLE users DROP COLUMN IF EXISTS {col};"))
    for col in ['full_name', 'phone', 'address_type']:
        op.execute(sa.text(f"ALTER TABLE user_addresses DROP COLUMN IF EXISTS {col};"))
    for col in ['razorpay_payment_id', 'delivered_at', 'subtotal',
                'discount_amount', 'delivery_fee', 'coupon_code', 'gift_message']:
        op.execute(sa.text(f"ALTER TABLE orders DROP COLUMN IF EXISTS {col};"))
    for col in ['color', 'color_hex', 'image']:
        op.execute(sa.text(f"ALTER TABLE order_items DROP COLUMN IF EXISTS {col};"))
    for col in ['color', 'color_hex', 'image']:
        op.execute(sa.text(f"ALTER TABLE cart_items DROP COLUMN IF EXISTS {col};"))
    for col in ['order_id', 'comment', 'helpful_count', 'is_approved', 'created_at']:
        op.execute(sa.text(f"ALTER TABLE ratings DROP COLUMN IF EXISTS {col};"))

    op.execute(sa.text("DROP TABLE IF EXISTS categories CASCADE;"))
