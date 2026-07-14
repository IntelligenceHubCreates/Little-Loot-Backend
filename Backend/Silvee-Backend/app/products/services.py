from typing import List
from sqlalchemy.orm import Session

from app.products.models import Product
from app.products.schemas import ProductCreate, ProductUpdate

def get_products(db: Session, skip: int = 0, limit: int = 100) -> List[Product]:
    """Get all products with pagination"""
    return db.query(Product).offset(skip).limit(limit).all()

def get_product(db: Session, product_id: str) -> Product:
    """Get product by ID"""
    return db.query(Product).filter(Product.id == product_id).first()

def create_product(db: Session, product: ProductCreate) -> Product:
    """Create new product"""
    db_product = Product(**product.model_dump())
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    return db_product

def update_product(
    db: Session,
    product_id: str,
    product: ProductUpdate
) -> Product:
    """Update product"""
    db_product = get_product(db, product_id)
    if not db_product:
        return None
    
    for key, value in product.model_dump(exclude_unset=True).items():
        setattr(db_product, key, value)
    
    db.commit()
    db.refresh(db_product)
    return db_product

def delete_product(db: Session, product_id: str) -> bool:
    """Delete product"""
    db_product = get_product(db, product_id)
    if not db_product:
        return False
    
    db.delete(db_product)
    db.commit()
    return True 

# app/products/crud.py
# ADD at the bottom — leave all existing functions untouched

def get_product_variants(db: Session, product_id: str) -> List[Product]:
    """
    Returns all active color variants of a product — all products that share
    the same variant_group_id, including the product itself.
    Returns an empty list if the product has no variant_group_id set.
    """
    product = get_product(db, product_id)

    # No product found, or no variant group — nothing to return
    if not product or not product.variant_group_id:
        return []

    return (
        db.query(Product)
        .filter(
            Product.variant_group_id == product.variant_group_id,
            Product.is_active.is_(True),
        )
        .order_by(Product.color.asc())
        .all()
    )