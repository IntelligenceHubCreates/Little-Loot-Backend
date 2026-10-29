from typing import Literal, Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_server: str
    postgres_port: int
    postgres_user: str
    postgres_password: str
    postgres_db: str
    cloudinary_api_secret: str
    cloudinary_api_key: str
    cloudinary_cloud_name: str
    secret_key: str
    algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = 1440  # 24 hours

    @field_validator("secret_key")
    @classmethod
    def secret_key_must_be_strong(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        return v

    # RazorPay Configuration
    razorpay_key_id: str
    razorpay_key_secret: str
    razorpay_webhook_secret: str

    # Email (Resend) — required for forgot-password emails
    resend_api_key: Optional[str] = None
    resend_from_email: str = "noreply@littleloot.in"
    # Customer-facing inbox: shown in email footers and used as Reply-To
    support_email: str = "little.loot0914@gmail.com"

    # Where reverse pickups (damage reports) are delivered — the store address.
    # Override with RETURN_WAREHOUSE_* env vars if it changes.
    return_warehouse_name: str    = "Little Loot"
    return_warehouse_phone: str   = "9346952257"
    return_warehouse_line1: str   = "Door No. 77-5-324, Ground Floor, Vanga Vari Street"
    return_warehouse_line2: str   = "Chowtra"
    return_warehouse_city: str    = "Guntur"
    return_warehouse_state: str   = "Andhra Pradesh"
    return_warehouse_pincode: str = "522003"
    frontend_url: str = "http://localhost:3000"

    class Config:
        env_file = ".env"


settings = Settings()
