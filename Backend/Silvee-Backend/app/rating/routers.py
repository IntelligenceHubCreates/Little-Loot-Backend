from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional

from app.db import get_db
from app.users.utils import JWTBearer
from app.rating.models import Rating
from app.products.models import Product

router = APIRouter(prefix='/api/rating', tags=["Ratings"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ReviewCreate(BaseModel):
    product_id: str
    order_id: Optional[str] = None
    rating: int = Field(..., ge=1, le=5)
    comment: str = Field(..., min_length=1)


class ReviewUpdate(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str = Field(..., min_length=1)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize(r: Rating, db: Session) -> dict:
    product = db.query(Product).filter(Product.id == r.product_id).first()
    return {
        "id": str(r.id),
        "product_id": str(r.product_id),
        "product": {
            "id": str(product.id),
            "name": getattr(product, "name", None),
            "product_image": getattr(product, "product_image", None),
            "original_price": getattr(product, "original_price", None),
        } if product else None,
        "rating": r.rating,
        "comment": r.comment,
        "order_id": str(r.order_id) if r.order_id else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "helpful_count": r.helpful_count or 0,
    }


def _require_user(user):
    if not user or 'id' not in user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user['id']


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/user")
async def get_user_reviews(user=Depends(JWTBearer()), db: Session = Depends(get_db)):
    """All reviews written by the current user."""
    uid = _require_user(user)
    reviews = (
        db.query(Rating)
        .filter(Rating.user_id == uid)
        .order_by(Rating.created_at.desc())
        .all()
    )
    return {"reviews": [_serialize(r, db) for r in reviews]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_review(payload: ReviewCreate, user=Depends(JWTBearer()), db: Session = Depends(get_db)):
    """Create a review (one per product per user)."""
    uid = _require_user(user)

    existing = (
        db.query(Rating)
        .filter(Rating.user_id == uid, Rating.product_id == payload.product_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You already reviewed this product")

    review = Rating(
        user_id=uid,
        product_id=payload.product_id,
        order_id=payload.order_id,
        rating=payload.rating,
        comment=payload.comment,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return {"id": str(review.id), "message": "Review submitted"}


@router.put("/{review_id}")
async def update_review(review_id: str, payload: ReviewUpdate, user=Depends(JWTBearer()), db: Session = Depends(get_db)):
    """Update an existing review (owner only)."""
    uid = _require_user(user)

    review = db.query(Rating).filter(Rating.id == review_id, Rating.user_id == uid).first()
    if not review:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")

    review.rating = payload.rating
    review.comment = payload.comment
    db.commit()
    return {"message": "Review updated"}


@router.delete("/{review_id}")
async def delete_review(review_id: str, user=Depends(JWTBearer()), db: Session = Depends(get_db)):
    """Delete a review (owner only)."""
    uid = _require_user(user)

    review = db.query(Rating).filter(Rating.id == review_id, Rating.user_id == uid).first()
    if not review:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")

    db.delete(review)
    db.commit()
    return {"message": "Review deleted"}