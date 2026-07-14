# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runtime

# postgresql-client: needed by app_entrypoint.sh for DB readiness check + psql seed
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpq5 postgresql-client curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for defense-in-depth
RUN groupadd --gid 1001 appgroup \
 && useradd  --uid 1001 --gid appgroup --no-create-home --shell /bin/bash appuser

# Copy Python packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy application code (see .dockerignore for exclusions)
COPY app/ ./app/
COPY alembic.ini ./
COPY alembic/    ./alembic/
COPY app_entrypoint.sh /usr/local/bin/app_entrypoint.sh
COPY wait-for-it.sh   /usr/local/bin/wait-for-it.sh

RUN sed -i 's/\r$//' /usr/local/bin/app_entrypoint.sh \
 && sed -i 's/\r$//' /usr/local/bin/wait-for-it.sh \
 && chmod +x /usr/local/bin/app_entrypoint.sh \
 && chmod +x /usr/local/bin/wait-for-it.sh \
 && chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/healthz || exit 1

ENTRYPOINT ["app_entrypoint.sh"]
