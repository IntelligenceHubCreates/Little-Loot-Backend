from typing import Optional
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
    algorithm: str
    access_token_expire_minutes: int = 10080  # 7 days; was 30 min (logged users out mid-session)

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
