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
    frontend_url: str = "http://localhost:3000"

    class Config:
        env_file = ".env"


settings = Settings()
