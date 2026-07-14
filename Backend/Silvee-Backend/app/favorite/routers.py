from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db import get_db
from app.users.utils import JWTBearer
from app.favorite.models import Favorite
from app.products.models import Product, ProductBase
from sqlalchemy import and_

router = APIRouter(prefix='/api/favorite', tags=["Favorites"])

@router.post("/{product_id}", status_code=status.HTTP_201_CREATED)
async def add_to_favorites(
    product_id: str,
    user=Depends(JWTBearer()),
    db: Session = Depends(get_db)
):
    """Add a product to user's favorites"""
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    
    # Check if product exists
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )
    
    # Check if already in favorites
    existing_favorite = db.query(Favorite).filter(
        and_(
            Favorite.user_id == user['id'],
            Favorite.product_id == product_id
        )
    ).first()
    
    if existing_favorite:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product already in favorites"
        )
    
    # Create favorite
    new_favorite = Favorite(
        user_id=user['id'],
        product_id=product_id
    )
    
    db.add(new_favorite)
    db.commit()
    db.refresh(new_favorite)
    
    return {
        "message": "Product added to favorites",
        "favorite_id": str(new_favorite.id)
    }

@router.delete("/{product_id}", status_code=status.HTTP_200_OK)
async def remove_from_favorites(
    product_id: str,
    user=Depends(JWTBearer()),
    db: Session = Depends(get_db)
):
    """Remove a product from user's favorites"""
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    
    # Find and delete favorite
    favorite = db.query(Favorite).filter(
        and_(
            Favorite.user_id == user['id'],
            Favorite.product_id == product_id
        )
    ).first()
    
    if not favorite:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not in favorites"
        )
    
    db.delete(favorite)
    db.commit()
    
    return {"message": "Product removed from favorites"}

@router.get("", response_model=dict)
async def get_user_favorites(
    user=Depends(JWTBearer()),
    db: Session = Depends(get_db)
):
    """Get all favorite products for the authenticated user"""
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    
    # Get all favorites for user with product details
    favorites = db.query(Favorite).filter(
        Favorite.user_id == user['id']
    ).all()
    
    # Get product details for each favorite
    favorite_products = []
    for favorite in favorites:
        product = db.query(Product).filter(Product.id == favorite.product_id).first()
        if product:
            favorite_products.append({
                "favorite_id": str(favorite.id),
                "product": ProductBase.from_orm(product)
            })
    
    return {
        "favorites": favorite_products,
        "count": len(favorite_products)
    }

@router.get("/check/{product_id}", response_model=dict)
async def check_if_favorite(
    product_id: str,
    user=Depends(JWTBearer()),
    db: Session = Depends(get_db)
):
    """Check if a product is in user's favorites"""
    
    if not user:
        return {"is_favorite": False}
    
    favorite = db.query(Favorite).filter(
        and_(
            Favorite.user_id == user['id'],
            Favorite.product_id == product_id
        )
    ).first()
    
    return {"is_favorite": favorite is not None}
