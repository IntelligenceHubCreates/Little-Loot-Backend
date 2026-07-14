# app/store_settings/models.py
"""
Store-preference key/value table. Stores ONLY non-secret store settings
(store name, shipping thresholds, toggles, public analytics IDs).
Secrets (Razorpay/SMTP/Cloudinary credentials) live in environment config
(app/settings.py) and are NEVER stored here or sent to the client.
"""
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime
from app.db import Base


class StoreSetting(Base):
    __tablename__ = "store_settings"

    key        = Column(String(80), primary_key=True)
    value      = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)