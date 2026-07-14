import os
from logging.config import fileConfig

from sqlalchemy import create_engine, engine_from_config
from sqlalchemy import pool

from alembic import context

from app.users import models as user_models
from app.products import models as product_models
from app.rating import models as rating_models
from app.favorite import models as favorite_models
from app.orders import models as orders_models
from app.cart import models as cart_models
from app.payments import models as payment_models
from app.models import Base

config = context.config

# Pull DATABASE_URL from env but do NOT pass through config.set_main_option:
# configparser uses % as an interpolation prefix, so passwords containing %
# raise ValueError. We store the URL in a plain variable and inject it directly
# into the engine / context below, bypassing configparser entirely.
_db_url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_db_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
