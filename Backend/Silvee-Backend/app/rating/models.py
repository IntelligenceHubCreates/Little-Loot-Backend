from app.models import Base
from sqlalchemy import Column, ForeignKey, Integer, Text, func, text, Boolean
from sqlalchemy.sql.sqltypes import TIMESTAMP
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID


class Rating(Base):
    """
    Product review/rating.

    REQUIRED: `Users.ratings = relationship("Rating", back_populates="user")`
    already exists in app/users/models.py, so this class MUST be importable at
    startup or every ORM query fails to initialise the Users mapper.

    NOTE: confirm the ForeignKey table names below match your actual
    __tablename__ values (products / orders). They follow the same convention
    as the existing models (users -> "users").
    """
    __tablename__ = "ratings"

    id            = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"), index=True, unique=True)
    user_id       = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    product_id    = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    order_id      = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True)
    rating        = Column(Integer, nullable=False)
    comment       = Column(Text, nullable=False, server_default="")
    helpful_count = Column(Integer, nullable=False, server_default="0")
    is_approved   = Column(Boolean, nullable=False, server_default=text("true"))  # admin moderation
    created_at    = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    user = relationship("Users", back_populates="ratings")
    product = relationship("Product", back_populates="ratings")