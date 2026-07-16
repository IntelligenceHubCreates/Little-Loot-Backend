#!/bin/bash
# Production entrypoint for Little Loot FastAPI backend.
# set -e: any failure aborts startup — prevents a silent partial-boot.
set -e

# ── Wait for the database TCP port to open ─────────────────────────────────────
wait-for-it.sh "${POSTGRES_SERVER}:${POSTGRES_PORT}" --timeout=60 --strict -- echo "DB port is open"

# ── Wait for PostgreSQL to accept queries ──────────────────────────────────────
echo "Waiting for PostgreSQL to be ready..."
export PGPASSWORD="${POSTGRES_PASSWORD}"
until psql -h "${POSTGRES_SERVER}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -c '\q' 2>/dev/null; do
  echo "  PostgreSQL not ready yet — retrying in 2s..."
  sleep 2
done
echo "PostgreSQL is ready."

# ── Run Alembic migrations ─────────────────────────────────────────────────────
# DATABASE_URL is read by alembic/env.py; if not set, env vars above are used.
echo "Running database migrations..."
alembic upgrade head
echo "Migrations complete."

# ── Seed initial admin account (only if env vars are provided) ─────────────────
# Set INITIAL_ADMIN_EMAIL and INITIAL_ADMIN_PASSWORD_HASH in AWS Secrets Manager.
# Generate hash: python -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('yourpassword'))"
ADMIN_EMAIL="${INITIAL_ADMIN_EMAIL:-}"
ADMIN_HASH="${INITIAL_ADMIN_PASSWORD_HASH:-}"

if [ -n "$ADMIN_EMAIL" ] && [ -n "$ADMIN_HASH" ]; then
  echo "Seeding admin account: ${ADMIN_EMAIL}"
  # Writeable CTE: UPDATE if email exists (never touches id → no FK violations),
  # INSERT only if no row was updated. Avoids ON CONFLICT which triggers FK checks.
  ADMIN_HASH_ESC=$(printf '%s' "${ADMIN_HASH}" | sed "s/'/''/g")
  ADMIN_EMAIL_ESC=$(printf '%s' "${ADMIN_EMAIL}" | sed "s/'/''/g")
  psql -h "${POSTGRES_SERVER}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -c "
    WITH upd AS (
      UPDATE users
      SET hashed_password = '${ADMIN_HASH_ESC}',
          role            = 1,
          is_active       = true,
          confirmed       = true
      WHERE email = '${ADMIN_EMAIL_ESC}'
      RETURNING id
    )
    INSERT INTO users (email, confirmed, hashed_password, role, is_active)
    SELECT '${ADMIN_EMAIL_ESC}', true, '${ADMIN_HASH_ESC}', 1, true
    WHERE NOT EXISTS (SELECT 1 FROM upd);"
  echo "Admin account seeded: ${ADMIN_EMAIL}"
else
  echo "INITIAL_ADMIN_EMAIL / INITIAL_ADMIN_PASSWORD_HASH not set — skipping admin seed."
fi

# ── Start server ───────────────────────────────────────────────────────────────
WORKERS="${GUNICORN_WORKERS:-2}"
PORT="${PORT:-8000}"

echo "Starting Gunicorn with ${WORKERS} Uvicorn workers on port ${PORT}..."
exec gunicorn \
  --workers "${WORKERS}" \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind "0.0.0.0:${PORT}" \
  --timeout 120 \
  --graceful-timeout 30 \
  --keep-alive 5 \
  --access-logfile - \
  --error-logfile - \
  --log-level info \
  app.main:server
