import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.settings import settings

# Prefer DATABASE_URL env var (Secrets Manager / App Runner injected);
# fall back to individual POSTGRES_* vars for local Docker Compose.
_database_url = os.getenv("DATABASE_URL") or (
    f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
    f"@{settings.postgres_server}:{settings.postgres_port}/{settings.postgres_db}"
)

engine = create_engine(
    _database_url,
    pool_pre_ping=True,      # detect stale connections (critical for RDS)
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800,       # recycle before RDS idle-timeout kills the socket
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    # Schema is managed exclusively by Alembic (alembic upgrade head).
    # This function is a no-op kept for import compatibility.
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_db_manually():
    db = SessionLocal()
    return db