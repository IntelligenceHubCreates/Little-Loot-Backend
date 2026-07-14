import os
import logging

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.limiter import limiter
from app.users.routers import router as user_router
from app.products.routers import router as product_router, category_router
from app.orders.routers import router as order_router
from app.users.address_router import router as address_router
from app.users.profile_router import router as profile_router
from app.cart.routers import router as cart_router
from app.favorite.routers import router as favorite_router
from app.rating.routers import router as rating_router
from app.newsletter.routers import newsletter_router
from app.coupons.routers import coupon_router
from app import models, schemas
from app.db import SessionLocal, engine, get_db, init_db
from app.schemas import Greeting
from app.users import models as user_models
from fastapi.middleware.cors import CORSMiddleware
from app.admin.routers import admin_router, category_write_router
from app.payments.routers import payment_router
from app.blog.routers import blog_router
from app.admin.analytics_router import analytics_router
from app.returns.routers import returns_router, admin_returns_router
from app.notifications.routers import notifications_router
from app.shipping.routers import (
    admin_shipping_router, admin_couriers_router,
    shipping_router, shiprocket_webhook_router,
)
from app.models import Base

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# REMOVED: Base.metadata.create_all() silently created tables bypassing Alembic
# and masked the fact that many tables had no migration.
# Schema is now managed exclusively by: alembic upgrade head

# ── Environment ───────────────────────────────────────────────────────────────
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
IS_PRODUCTION = ENVIRONMENT == "production"

# SECURITY: /docs and /redoc must be disabled in production — they expose the
# full API schema including auth endpoint shapes to any visitor.
server = FastAPI(
    title="Little Loot API",
    version="1.0.0",
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)

# ── Rate limiter ─────────────────────────────────────────────────────────────
server.state.limiter = limiter
server.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ─────────────────────────────────────────────────────────────────────
# SECURITY: Never use allow_origins=["*"] with allow_credentials=True.
# That combination is rejected by browsers AND silently allows credential
# leakage in some server-side-fetch scenarios.
# Read allowed origins from env; default to localhost only for dev.
_raw_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000"
)
allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# A-9: Warn loudly if ALLOWED_ORIGINS is not explicitly set in production
if IS_PRODUCTION and "ALLOWED_ORIGINS" not in os.environ:
    logger.warning(
        "ALLOWED_ORIGINS is not set in production — CORS is restricted to localhost only. "
        "Set ALLOWED_ORIGINS=https://littleloot.in to allow your frontend."
    )

server.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With"],
    expose_headers=["Content-Length"],
    max_age=600,
)

# ── Security headers middleware ───────────────────────────────────────────────
@server.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"]    = "nosniff"
    response.headers["X-Frame-Options"]           = "DENY"
    response.headers["Referrer-Policy"]           = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"]        = "camera=(), microphone=(), geolocation=()"
    if IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
        # A-12: CSP for pure JSON API — no HTML content served in production
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response

# ── Startup validation ────────────────────────────────────────────────────────
@server.on_event("startup")
async def validate_production_config():
    if not IS_PRODUCTION:
        return
    missing = []
    # A-8: Resend required in production so forgot-password emails actually send
    if not os.getenv("RESEND_API_KEY"):
        missing.append("RESEND_API_KEY")
    if not os.getenv("NEXTAUTH_SECRET") and not os.getenv("SECRET_KEY"):
        missing.append("SECRET_KEY / NEXTAUTH_SECRET")
    if missing:
        logger.error(
            "PRODUCTION MISCONFIGURATION: the following required env vars are not set: %s. "
            "Features depending on them will silently fail.",
            ", ".join(missing),
        )

# ── Global error handler — never leak internal exception text ────────────────
@server.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "An internal error occurred."})

# ── Routers ───────────────────────────────────────────────────────────────────
server.include_router(user_router)
server.include_router(product_router)
server.include_router(category_router)
server.include_router(order_router)
server.include_router(address_router)
server.include_router(profile_router)
server.include_router(cart_router)
server.include_router(favorite_router)
server.include_router(rating_router)
server.include_router(admin_router)
server.include_router(category_write_router)
server.include_router(payment_router)
server.include_router(newsletter_router)
server.include_router(coupon_router)
server.include_router(blog_router)
server.include_router(analytics_router)
server.include_router(returns_router)
server.include_router(admin_returns_router)
server.include_router(notifications_router)
server.include_router(admin_shipping_router)
server.include_router(admin_couriers_router)
server.include_router(shipping_router)
server.include_router(shiprocket_webhook_router)


@server.get("/")
async def root():
    return {"message": "Welcome to Little Loot API"}


@server.get("/healthz", tags=["Health"])
async def healthz(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8001))
    uvicorn.run(server, host="0.0.0.0", port=port)
