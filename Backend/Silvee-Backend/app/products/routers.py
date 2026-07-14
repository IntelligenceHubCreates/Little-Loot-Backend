# app/products/routers.py
"""
FIX: Subcategory filtering now works correctly.

Root cause was:
  1. all_child_ids did N+1 DB queries (Python loop over lazy-loaded children)
  2. get_products_by_category_slug didn't pass sub_category_slug to filter
  3. get_product_list ?category_slug param wasn't wired to the CTE function

All fixed below using the get_category_ids() PostgreSQL CTE function.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
from typing import List, Optional
from uuid import UUID

import cloudinary
import cloudinary.uploader
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import or_, text, func
from sqlalchemy.orm import Session

from pydantic import BaseModel as PydanticBase
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status

from app.db import get_db
from app.products.models import (
    Category, CategoryBase, CategoryWithChildren,
    Product, ProductBase, ProductListResponse,
)
from app.settings import settings
from app.shared.services import upload_images
from app.users.utils import JWTBearer
from app.rating.models import Rating

cloudinary.config(
    cloud_name=settings.cloudinary_cloud_name,
    api_key=settings.cloudinary_api_key,
    api_secret=settings.cloudinary_api_secret,
)

product_router  = APIRouter(prefix="/api/product",    tags=["Products"])
category_router = APIRouter(prefix="/api/categories", tags=["Categories"])

# Alias so main.py's `from app.products.routers import router as product_router` keeps working
router = product_router

class LinkVariantsPayload(PydanticBase):
    variant_ids:      List[str]  # list of product UUID strings
    variant_group_id: str        # shared key e.g. "grp_jelly_backpack"


# ── Helpers ───────────────────────────────────────────────────────

def _get_all_category_ids(slug: str, session: Session) -> list[UUID]:
    """
    Single SQL CTE call that returns the category + all descendants.
    Replaces the broken Python N+1 all_child_ids loop.
    """
    rows = session.execute(
        text("SELECT category_id FROM get_category_ids(:slug)"),
        {"slug": slug}
    ).fetchall()
    return [row[0] for row in rows]


def _apply_sort(q, sort_by: str):
    if sort_by == "price_asc":
        return q.order_by(Product.original_price.asc())
    elif sort_by == "price_desc":
        return q.order_by(Product.original_price.desc())
    elif sort_by == "newest":
        return q.order_by(Product.created_at.desc())
    else:  # featured (default)
        return q.order_by(Product.is_featured.desc(), Product.created_at.desc())


def _require_admin(user):
    if user is None or user.get("role") != 1:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorised")


def _get_product_or_404(product_id: str, session: Session) -> Product:
    try:
        uid = UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid product ID")
    product = session.query(Product).filter(Product.id == uid).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


def _resolve_category(name: Optional[str], slug: Optional[str], session: Session) -> Optional[UUID]:
    if slug:
        cat = session.query(Category).filter(Category.slug == slug).first()
        return cat.id if cat else None
    if name:
        cat = session.query(Category).filter(Category.name.ilike(name)).first()
        return cat.id if cat else None
    return None


def _sync_product_subcategory(product: Product, session: Session) -> None:
    """Keep sub_category_slug + sub_category_name in sync when category_id changes."""
    if product.category_id:
        cat = session.query(Category).filter(Category.id == product.category_id).first()
        if cat:
            product.sub_category_slug = cat.slug
            product.sub_category_name = cat.name


# ═══════════════════════════════════════════════════════════════════
#  CATEGORY ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@category_router.get("/", response_model=List[CategoryWithChildren])
async def list_categories(session: Session = Depends(get_db)):
    roots = (
        session.query(Category)
        .filter(Category.parent_id.is_(None), Category.is_active.is_(True))
        .order_by(Category.sort_order)
        .all()
    )
    return [CategoryWithChildren.from_orm(c) for c in roots]


@category_router.get("/{slug}", response_model=CategoryWithChildren)
async def get_category(slug: str, session: Session = Depends(get_db)):
    cat = session.query(Category).filter(
        Category.slug == slug, Category.is_active.is_(True)
    ).first()
    if not cat:
        raise HTTPException(status_code=404, detail=f"Category '{slug}' not found")
    return CategoryWithChildren.from_orm(cat)


@category_router.get("/{slug}/products", response_model=ProductListResponse)
async def get_products_by_category_slug(
    slug: str,
    limit:    int           = Query(20, ge=1, le=100),
    skip:     int           = Query(0,  ge=0),
    sort_by:  str           = Query("featured", regex="^(featured|price_asc|price_desc|newest|rating)$"),
    sub_slug: Optional[str] = Query(None, description="Sub-category slug e.g. 'puzzles'"),
    in_stock: Optional[bool] = Query(None),
    on_sale:  Optional[bool] = Query(None),
    session:  Session       = Depends(get_db),
):
    effective_slug = sub_slug if sub_slug else slug
    all_ids = _get_all_category_ids(effective_slug, session)

    if not all_ids:
        return ProductListResponse(data=[], totalCount=0)

    q = session.query(Product).filter(
        Product.is_active.is_(True),
        Product.category_id.in_(all_ids),
    )

    if in_stock:
        q = q.filter(Product.count > 0)
    if on_sale:
        q = q.filter(Product.amount_discount > 0)

    total = q.count()
    def _apply_sort(q, sort_by: str):
        if sort_by == "price_asc":
            return q.order_by(Product.original_price.asc(), Product.id.asc())
        elif sort_by == "price_desc":
            return q.order_by(Product.original_price.desc(), Product.id.asc())
        elif sort_by == "newest":
            return q.order_by(Product.created_at.desc(), Product.id.asc())
        else:  # featured (default)
            return q.order_by(
                Product.is_featured.desc(),
                Product.created_at.desc(),
                Product.id.asc(),
            )

    products = q.offset(skip).limit(limit).all()

    return ProductListResponse(
        data=[ProductBase.from_orm(p) for p in products],
        totalCount=total,
        page=(skip // limit) + 1 if limit else 1,
        limit=limit,
    )

# ═══════════════════════════════════════════════════════════════════
#  CATEGORY ADMIN ENDPOINTS  (Phase 6)
#  These did not exist before — create/update/delete + admin list.
# ═══════════════════════════════════════════════════════════════════

class CategoryCreatePayload(PydanticBase):
    name:        str
    slug:        str
    parent_id:   Optional[str] = None
    emoji:       Optional[str] = None
    description: Optional[str] = None
    sort_order:  int           = 0
    is_active:   bool          = True


class CategoryUpdatePayload(PydanticBase):
    # All optional → supports partial updates (e.g. the toggle sends only is_active)
    name:        Optional[str]  = None
    slug:        Optional[str]  = None
    parent_id:   Optional[str]  = None
    emoji:       Optional[str]  = None
    description: Optional[str]  = None
    sort_order:  Optional[int]  = None
    is_active:   Optional[bool] = None


def _category_to_dict(c: Category, product_count: int = 0) -> dict:
    """Admin category shape — includes product_count and is_active (always)."""
    return {
        "id":          str(c.id),
        "name":        c.name,
        "slug":        c.slug,
        "parent_id":   str(c.parent_id) if c.parent_id else None,
        "emoji":       c.emoji,
        "description": c.description,
        "sort_order":  c.sort_order,
        "is_active":   c.is_active,
        "product_count": product_count,
        "children":    [],   # admin list is flat; frontend rebuilds the tree
    }


@category_router.get("/admin/list")
async def admin_list_categories(
    include_inactive: bool = Query(True, description="Admin: include inactive categories"),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    """
    Flat list of ALL categories (roots + children, active + inactive by default)
    WITH a real product_count per category. The storefront GET / stays active-only;
    this is the admin view so deactivated categories remain visible and toggle-able.
    """
    _require_admin(user)

    q = session.query(Category)
    if not include_inactive:
        q = q.filter(Category.is_active.is_(True))
    cats = q.order_by(Category.sort_order, Category.name).all()

    # One grouped COUNT for all categories (avoids N queries)
    counts = dict(
        session.query(Product.category_id, func.count(Product.id))
        .group_by(Product.category_id)
        .all()
    )

    return {
        "data": [_category_to_dict(c, int(counts.get(c.id, 0))) for c in cats],
        "totalCount": len(cats),
    }

@category_router.post("", status_code=201)
async def create_category(
    payload: CategoryCreatePayload,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)

    name = payload.name.strip()
    slug = payload.slug.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    if not slug:
        raise HTTPException(status_code=400, detail="Slug is required")

    # Slug must be unique (DB also enforces it, but give a clean 409)
    if session.query(Category).filter(Category.slug == slug).first():
        raise HTTPException(status_code=409, detail=f"Slug '{slug}' already exists")

    # Validate parent (and enforce 2-level depth: parent must itself be a root)
    parent_uuid = None
    if payload.parent_id:
        try:
            parent_uuid = UUID(payload.parent_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid parent_id")
        parent = session.query(Category).filter(Category.id == parent_uuid).first()
        if not parent:
            raise HTTPException(status_code=400, detail="Parent category not found")
        if parent.parent_id is not None:
            raise HTTPException(status_code=400, detail="Only two levels allowed: a sub-category cannot be a parent")

    cat = Category(
        name=name,
        slug=slug,
        parent_id=parent_uuid,
        emoji=(payload.emoji or None),
        description=(payload.description or None),
        sort_order=payload.sort_order or 0,
        is_active=payload.is_active,
    )
    session.add(cat)
    session.commit()
    session.refresh(cat)
    return _category_to_dict(cat, 0)


@category_router.put("/{id}")
async def update_category(
    id: str,
    payload: CategoryUpdatePayload,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)

    try:
        uid = UUID(id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid category ID")

    cat = session.query(Category).filter(Category.id == uid).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")

    if payload.slug is not None:
        new_slug = payload.slug.strip().lower()
        if new_slug and new_slug != cat.slug:
            if session.query(Category).filter(Category.slug == new_slug, Category.id != uid).first():
                raise HTTPException(status_code=409, detail=f"Slug '{new_slug}' already exists")
            cat.slug = new_slug

    if payload.parent_id is not None:
        if payload.parent_id == "":
            cat.parent_id = None
        else:
            try:
                p_uid = UUID(payload.parent_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid parent_id")
            if p_uid == uid:
                raise HTTPException(status_code=400, detail="A category cannot be its own parent")
            parent = session.query(Category).filter(Category.id == p_uid).first()
            if not parent:
                raise HTTPException(status_code=400, detail="Parent category not found")
            if parent.parent_id is not None:
                raise HTTPException(status_code=400, detail="Only two levels allowed")
            # Prevent making a category a child of its own descendant
            if any(child.id == p_uid for child in cat.children):
                raise HTTPException(status_code=400, detail="Cannot set a child as the parent")
            cat.parent_id = p_uid

    if payload.name        is not None: cat.name        = payload.name.strip() or cat.name
    if payload.emoji       is not None: cat.emoji       = payload.emoji or None
    if payload.description is not None: cat.description = payload.description or None
    if payload.sort_order  is not None: cat.sort_order  = payload.sort_order
    if payload.is_active   is not None: cat.is_active   = payload.is_active

    session.commit()
    session.refresh(cat)

    count = session.query(func.count(Product.id)).filter(Product.category_id == uid).scalar() or 0
    return _category_to_dict(cat, int(count))

@category_router.delete("/{id}")
async def delete_category(
    id: str,
    force: bool = Query(False, description="Force-delete a category that has sub-categories (re-parents them)"),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    """
    Delete policy (D1 = b):
      • If the category has ANY products → 409, never deletable here.
        The admin must reassign/delete those products first.
      • If it has sub-categories and force=false → 409 with child_count.
      • If force=true → re-parent children to THIS category's parent, then delete.
    """
    _require_admin(user)

    try:
        uid = UUID(id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid category ID")

    cat = session.query(Category).filter(Category.id == uid).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")

    product_count = session.query(func.count(Product.id)).filter(Product.category_id == uid).scalar() or 0
    children = session.query(Category).filter(Category.parent_id == uid).all()
    child_count = len(children)

    # Policy b: products block deletion unconditionally (force does NOT override this)
    if product_count > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"Cannot delete: {product_count} product(s) use this category. Reassign or delete them first.",
                "product_count": int(product_count),
                "child_count": child_count,
                "reason": "has_products",
            },
        )

    # Subcategories: require explicit force, then re-parent them up one level
    if child_count > 0 and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"This category has {child_count} sub-categor(y/ies). Confirm to delete and move them up.",
                "product_count": 0,
                "child_count": child_count,
                "reason": "has_children",
            },
        )

    if child_count > 0 and force:
        for child in children:
            child.parent_id = cat.parent_id   # promote to this node's parent (or root if None)
        session.flush()

    session.delete(cat)
    session.commit()
    return {"deleted": True, "id": id, "reparented_children": child_count if force else 0}


# ═══════════════════════════════════════════════════════════════════
#  PRODUCT ENDPOINTS
#  IMPORTANT: /featured, /search, /all, /admin/top MUST be before /{id}
# ═══════════════════════════════════════════════════════════════════

@product_router.get("", response_model=dict)
async def root(session: Session = Depends(get_db), user=Depends(JWTBearer())):
    """Health-check route (requires auth)."""
    return {"test": "Hello World"}


@product_router.get("/featured", response_model=ProductListResponse)
async def get_featured_products(
    limit: int = Query(8, ge=1, le=50),
    session: Session = Depends(get_db),
):
    products = (
        session.query(Product)
        .filter(Product.is_active.is_(True), Product.is_featured.is_(True))
        .order_by(Product.created_at.desc())
        .limit(limit)
        .all()
    )
    return ProductListResponse(data=[ProductBase.from_orm(p) for p in products], totalCount=len(products))


@product_router.get("/search", response_model=ProductListResponse)
async def search_products(
    q:       str           = Query(..., min_length=2, description="Search query"),
    limit:   int           = Query(8,   ge=1, le=50),
    skip:    int           = Query(0,   ge=0),
    sort_by: str           = Query("featured"),
    session: Session       = Depends(get_db),
):
    """
    Full-text search across product name, category, description, brand.
    sort_by: featured (relevance), price_asc, price_desc, newest
    """
    term = f"%{q.strip()}%"
    prefix_term = f"{q.strip()}%"

    base_q = session.query(Product).filter(
        Product.is_active.is_(True),
        or_(
            Product.name.ilike(term),
            Product.category.ilike(term),
            Product.sub_category_name.ilike(term),
            Product.description.ilike(term),
            Product.brand.ilike(term),
        )
    )

    if sort_by == "price_asc":
        results_q = base_q.order_by(Product.original_price.asc())
    elif sort_by == "price_desc":
        results_q = base_q.order_by(Product.original_price.desc())
    elif sort_by == "newest":
        results_q = base_q.order_by(Product.created_at.desc())
    else:
        results_q = base_q.order_by(
            Product.name.ilike(prefix_term).desc(),
            Product.is_featured.desc(),
            Product.name.ilike(term).desc(),
            Product.created_at.desc(),
        )

    results = results_q.offset(skip).limit(limit).all()

    # Count without limit for pagination (cap at 200 to avoid full scans)
    total = (
        session.query(Product)
        .filter(
            Product.is_active.is_(True),
            or_(
                Product.name.ilike(term),
                Product.category.ilike(term),
                Product.sub_category_name.ilike(term),
                Product.description.ilike(term),
                Product.brand.ilike(term),
            )
        )
        .limit(200)
        .count()
    )

    return ProductListResponse(
        data=[ProductBase.from_orm(p) for p in results],
        totalCount=total,
        page=(skip // limit) + 1 if limit else 1,
        limit=limit,
    )


@product_router.get("/all", response_model=ProductListResponse)
async def get_product_list(
    limit:          int           = Query(12, ge=1, le=100),
    skip:           int           = Query(0,  ge=0),
    category:       Optional[str] = Query(None),
    category_slug:  Optional[str] = Query(None),
    sub_slug:       Optional[str] = Query(None, description="Sub-category slug for deeper filtering"),
    search:         Optional[str] = Query(None, description="Admin search: name / category / brand"),
    in_stock:       Optional[bool] = Query(None),
    on_sale:        Optional[bool] = Query(None),
    is_new:         Optional[bool] = Query(None),
    is_featured:    Optional[bool] = Query(None),
    is_active:      Optional[bool] = Query(None, description="None=active only (storefront); pass explicitly for admin"),
    include_inactive: bool        = Query(False, description="Admin: include inactive products"),
    stock_status:   Optional[str] = Query(None, regex="^(in_stock|low_stock|out_of_stock)$"),
    low_stock_threshold: int      = Query(10, ge=0, le=1000),
    min_price:      Optional[int]  = Query(None, ge=0),
    max_price:      Optional[int]  = Query(None, ge=0),
    sort_by:        str           = Query("featured", regex="^(featured|price_asc|price_desc|newest|rating|stock_asc|stock_desc)$"),
    session:        Session       = Depends(get_db),
):
    q = session.query(Product)

    # ── Active visibility ─────────────────────────────────────────
    # Storefront (default): active only. Admin passes include_inactive=true
    # to see everything, or is_active=false to list ONLY inactive products.
    if is_active is not None:
        q = q.filter(Product.is_active.is_(is_active))
    elif not include_inactive:
        q = q.filter(Product.is_active.is_(True))

    effective_slug = sub_slug or category_slug

    if effective_slug:
        all_ids = _get_all_category_ids(effective_slug, session)
        if not all_ids:
            return ProductListResponse(data=[], totalCount=0)
        q = q.filter(Product.category_id.in_(all_ids))
    elif category:
        parent_cat = session.query(Category).filter(
            Category.name.ilike(f"%{category}%"), Category.is_active.is_(True)
        ).first()
        if parent_cat:
            all_ids = _get_all_category_ids(parent_cat.slug, session)
            q = q.filter(or_(
                Product.category_id.in_(all_ids),
                Product.category.ilike(f"%{category}%"),
            ))
        else:
            q = q.filter(Product.category.ilike(f"%{category}%"))

    # ── Search (admin) — name / legacy category / brand ───────────
    if search and search.strip():
        term = f"%{search.strip()}%"
        q = q.filter(or_(
            Product.name.ilike(term),
            Product.category.ilike(term),
            Product.sub_category_name.ilike(term),
            Product.brand.ilike(term),
        ))

    if in_stock:
        q = q.filter(Product.count > 0)
    if on_sale:
        q = q.filter(Product.amount_discount > 0)
    if is_new:
        q = q.filter(Product.is_new.is_(True))
    if is_featured:
        q = q.filter(Product.is_featured.is_(True))

    # ── Stock status (mutually-aware with in_stock above) ─────────
    if stock_status == "out_of_stock":
        q = q.filter(Product.count <= 0)
    elif stock_status == "low_stock":
        q = q.filter(Product.count > 0, Product.count <= low_stock_threshold)
    elif stock_status == "in_stock":
        q = q.filter(Product.count > low_stock_threshold)

    if min_price is not None:
        q = q.filter((Product.original_price - Product.amount_discount) >= min_price)
    if max_price is not None:
        q = q.filter((Product.original_price - Product.amount_discount) <= max_price)

    total = q.count()

    # ── Sort (adds stock_asc / stock_desc) ────────────────────────
    if sort_by == "price_asc":
        q = q.order_by(Product.original_price.asc(), Product.id.asc())
    elif sort_by == "price_desc":
        q = q.order_by(Product.original_price.desc(), Product.id.asc())
    elif sort_by == "newest":
        q = q.order_by(Product.created_at.desc(), Product.id.asc())
    elif sort_by == "stock_asc":
        q = q.order_by(Product.count.asc(), Product.id.asc())
    elif sort_by == "stock_desc":
        q = q.order_by(Product.count.desc(), Product.id.asc())
    else:  # featured (default) — "rating" falls through to featured for now
        q = q.order_by(Product.is_featured.desc(), Product.created_at.desc(), Product.id.asc())

    products = q.offset(skip).limit(limit).all()

    return ProductListResponse(
        data=[ProductBase.from_orm(p) for p in products],
        totalCount=total,
        page=(skip // limit) + 1 if limit else 1,
        limit=limit,
    )

@product_router.get("/admin/top")
async def top_products(
    session: Session = Depends(get_db),
    user=Depends(JWTBearer()),
):
    """Top 5 products by units sold in last 30 days."""
    _require_admin(user)

    from app.orders.models import OrderItem
    from sqlalchemy import func

    since = datetime.utcnow() - timedelta(days=30)

    rows = session.query(
        Product,
        func.coalesce(func.sum(OrderItem.quantity), 0).label("units_sold")
    ).outerjoin(OrderItem, Product.id == OrderItem.product_id) \
     .group_by(Product.id) \
     .order_by(func.coalesce(func.sum(OrderItem.quantity), 0).desc()) \
     .limit(5).all()

    return [
        {
            "id":              str(p.id),
            "name":            p.name,
            "category":        p.category,
            "price_formatted": f"₹{float(p.original_price):,.0f}",
            "units_sold":      int(units),
            "emoji":           None,
            "gradient":        None,
        }
        for p, units in rows
    ]


@product_router.get("/{id}/variants", response_model=dict)
async def get_product_variants_endpoint(
    id: str,
    session: Session = Depends(get_db),
):
    """
    Returns all color variants of a product (siblings sharing variant_group_id).
    Returns {"variants": []} if the product has no variant group.
    Used by the product page color swatch UI — no auth required.
    """
    from app.products.crud import get_product_variants

    variants = get_product_variants(session, id)
    return {
        "variants": [ProductBase.from_orm(v).dict() for v in variants]
    }


@product_router.post("/admin/link-variants", status_code=200)
async def link_color_variants(
    payload: LinkVariantsPayload,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    """
    Admin: link multiple products as color variants of each other.
    Sets variant_group_id on all provided product IDs.
    Safe to call multiple times (idempotent).
    """
    _require_admin(user)

    if len(payload.variant_ids) < 2:
        raise HTTPException(
            status_code=400,
            detail="Provide at least 2 product IDs to link as variants"
        )

    updated = 0
    for pid in payload.variant_ids:
        try:
            uid = UUID(pid)
        except ValueError:
            continue
        product = session.query(Product).filter(Product.id == uid).first()
        if product:
            product.variant_group_id = payload.variant_group_id
            updated += 1

    session.commit()
    return {
        "success":  True,
        "linked":   updated,
        "group_id": payload.variant_group_id,
    }

@product_router.get("/{id}", response_model=dict)
async def get_product_detail(id: str, session: Session = Depends(get_db)):
    product = _get_product_or_404(id, session)

    # Aggregate live rating data so the product page shows real stars/counts
    # instead of the frontend's hard-coded 4.0 fallback.
    agg = (
        session.query(
            func.avg(Rating.rating).label("avg"),
            func.count(Rating.id).label("cnt"),
        )
        .filter(Rating.product_id == product.id)
        .one()
    )
    avg_rating = round(float(agg.avg), 1) if agg.avg is not None else 0.0
    review_count = int(agg.cnt or 0)

    payload = ProductBase.from_orm(product).dict()
    payload["rating"] = avg_rating
    payload["review_count"] = review_count
    return {"product_details": payload}
# ═══════════════════════════════════════════════════════════════════
#  ADMIN WRITE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@product_router.post("", response_model=ProductBase, status_code=201)
async def add_new_product(
    productName:           str                       = Form(...),
    productCategory:       str                       = Form(...),
    productCategorySlug:   Optional[str]             = Form(None),
    productDescription:    str                       = Form(...),
    productPrice:          int                       = Form(...),
    productCount:          int                       = Form(...),
    productDiscount:       int                       = Form(...),
    productDiscountAmount: int                       = Form(...),
    productImages:         Optional[List[UploadFile]] = File(None),
    productImageUrls:      Optional[List[str]]       = Form(None),
    productDetails:        Optional[List[str]]        = Form(None),
    offerExpirationDate:   Optional[datetime]        = Form(None),
    productColor:          Optional[str]    = Form(None),
    productColorHex:       Optional[str]    = Form(None),
    productVariantGroupId: Optional[str]    = Form(None),
    productColorVariants:  Optional[str]    = Form(None),
     productVideo:         Optional[str] = Form(None),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    if productPrice <= 0:
        raise HTTPException(status_code=400, detail="Price must be greater than 0")

    images = await upload_images(productImages) if productImages else []
    if productImageUrls:
        images += [{"url": u, "public_id": "direct_url"} for u in productImageUrls if u]

    category_id = _resolve_category(productCategory, productCategorySlug, session)

    new_product = Product(
        name=productName,
        category=productCategory,
        category_id=category_id,
        description=productDescription,
        original_price=productPrice,
        percentage_discount=productDiscount,
        amount_discount=productDiscountAmount,
        count=productCount,
        product_image=images,
        details=productDetails or [],
        offer_expiration_date=offerExpirationDate,
        color=productColor or None,
        color_hex=productColorHex or None,
        variant_group_id=productVariantGroupId or None,
        color_variants = json.loads(productColorVariants) if productColorVariants else [],
        product_video = productVideo or None,
    )
    _sync_product_subcategory(new_product, session)
    session.add(new_product)
    session.commit()
    session.refresh(new_product)
    return ProductBase.from_orm(new_product)


@product_router.post("/uploadfile/product/{id}", response_model=ProductBase)
async def upload_product_image(
    id: int,
    file: UploadFile = File(..., max_length=10_485_760),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in {"png", "jpg", "jpeg", "webp"}:
        raise HTTPException(status_code=400, detail="File type not allowed")
    result = cloudinary.uploader.upload(await file.read(), folder="littleloot/products", resource_type="image")
    product = session.query(Product).filter(Product.id == id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    images = list(product.product_image or [])
    images.insert(0, {"url": result["secure_url"], "public_id": result["public_id"]})
    product.product_image = images
    session.commit()
    session.refresh(product)
    return ProductBase.from_orm(product)

# Final clean version — replace upload_product_video in routers.py

@product_router.post("/upload/video")
async def upload_product_video(
    request: Request,
    user=Depends(JWTBearer()),
):
    _require_admin(user)
 
    ALLOWED   = {"mp4", "webm", "mov", "avi", "mkv"}
    MAX_BYTES = 200 * 1024 * 1024  # 200 MB
 
    try:
        form = await request.form()
        f    = form.get("file") or form.get("files")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid form data")
 
    if f is None:
        raise HTTPException(status_code=400, detail="No file field in request")
 
    filename = f.filename or "video"
    ext      = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
 
    if ext not in ALLOWED:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported type '{ext}'. Allowed: mp4, webm, mov, avi, mkv"
        )
 
    contents = await f.read()
 
    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Video file is empty")
 
    if len(contents) > MAX_BYTES:
        mb = len(contents) // (1024 * 1024)
        raise HTTPException(status_code=400, detail=f"Video is {mb} MB — exceeds 200 MB limit")
 
    try:
        import io
        if len(contents) > 10 * 1024 * 1024:
            result = cloudinary.uploader.upload_large(
                io.BytesIO(contents),
                folder="littleloot/products/videos",
                resource_type="video",
                chunk_size=6 * 1024 * 1024,
            )
        else:
            result = cloudinary.uploader.upload(
                contents,
                folder="littleloot/products/videos",
                resource_type="video",
            )
    except Exception:
        logger.error("Cloudinary video upload failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Video upload failed. Please try again.")
 
    return {
        "url":       result["secure_url"],
        "public_id": result["public_id"],
        "duration":  result.get("duration"),
        "format":    result.get("format"),
    }
 
@product_router.put("/{id}", response_model=ProductBase)
async def update_product(
    id:                    str,
    productName:           str                       = Form(...),
    productCategory:       str                       = Form(...),
    productCategorySlug:   Optional[str]             = Form(None),
    productDescription:    str                       = Form(...),
    productPrice:          int                       = Form(...),
    productCount:          int                       = Form(...),
    productDiscount:       int                       = Form(...),
    productDiscountAmount: int                       = Form(...),
    productImages:         List[UploadFile]          = File(None),
    productImageUrls:      List[str]                 = Form(None),
    productDetails:        List[str]                 = Form(...),
    oldProductImages:      str                       = Form(...),
    productColor:          Optional[str]    = Form(None),
    productColorHex:       Optional[str]    = Form(None),
    productVariantGroupId: Optional[str]    = Form(None),
    productColorVariants:  Optional[str]    = Form(None),
    productVideo:          Optional[str] = Form(None),
    deleteVideo:           Optional[str] = Form(None),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    if productPrice <= 0:
        raise HTTPException(status_code=400, detail="Price must be greater than 0")

    product = _get_product_or_404(id, session)

    existing_images = json.loads(oldProductImages) if oldProductImages else []
    new_uploads = await upload_images(productImages or [])
    if productImageUrls:
        new_uploads += [{"url": u, "public_id": "direct_url"} for u in productImageUrls if u]

    category_id = _resolve_category(productCategory, productCategorySlug, session)

    product.name                = productName
    product.category            = productCategory
    product.category_id         = category_id
    product.description         = productDescription
    product.original_price      = productPrice
    product.percentage_discount = productDiscount
    product.amount_discount     = productDiscountAmount
    product.count               = productCount
    product.product_image       = new_uploads + existing_images
    product.details             = productDetails

    if productColor is not None:
        product.color = productColor or None
    if productColorHex is not None:
        product.color_hex = productColorHex or None
    if productVariantGroupId is not None:
        product.variant_group_id = productVariantGroupId or None
    if productColorVariants is not None:                                      # ← ADD FROM HERE
        product.color_variants = json.loads(productColorVariants)     
    if deleteVideo == 'true':
        product.product_video = None
    elif productVideo is not None:
        product.product_video = productVideo or None


    _sync_product_subcategory(product, session)

    session.commit()
    session.refresh(product)
    return ProductBase.from_orm(product)


@product_router.delete("/{id}", status_code=204)
async def delete_product(id: str, user=Depends(JWTBearer()), session: Session = Depends(get_db)):
    _require_admin(user)
    product = _get_product_or_404(id, session)

    # Must delete order_items first — product_id is NOT NULL so SQLAlchemy
    # can't nullify it before delete, causing IntegrityError
    from app.orders.models import OrderItem
    session.query(OrderItem).filter(OrderItem.product_id == product.id).delete(synchronize_session=False)

    session.delete(product)
    session.commit()
# ═══════════════════════════════════════════════════════════════════
#  PASTE THIS AT THE VERY BOTTOM OF routers.py
#  after the delete_product function
# ═══════════════════════════════════════════════════════════════════

@product_router.post("/upload/images")
async def upload_color_images(
    files: List[UploadFile] = File(...),
    user=Depends(JWTBearer()),
):
    """
    Upload color variant images to Cloudinary.
    Called by the admin panel when adding images to a color variant.
    No DB session needed — only Cloudinary upload.
    """
    _require_admin(user)

    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    ALLOWED = {"png", "jpg", "jpeg", "webp", "gif"}
    MAX_SIZE = 10 * 1024 * 1024  # 10 MB per file

    uploaded = []

    for file in files:
        # Validate extension
        filename  = file.filename or "upload"
        ext       = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        if ext not in ALLOWED:
            raise HTTPException(
                status_code=400,
                detail=f"'{filename}': unsupported type '{ext}'. Allowed: png, jpg, jpeg, webp, gif"
            )

        # Read bytes
        try:
            contents = await file.read()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not read '{filename}': {exc}")

        if len(contents) > MAX_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"'{filename}' exceeds 10 MB limit"
            )

        if len(contents) == 0:
            raise HTTPException(
                status_code=400,
                detail=f"'{filename}' is empty"
            )

        # Upload to Cloudinary
        try:
            result = cloudinary.uploader.upload(
                contents,
                folder="littleloot/products/colors",
                resource_type="image",
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Cloudinary upload failed for '{filename}': {exc}"
            )

        uploaded.append({
            "url":       result["secure_url"],
            "public_id": result["public_id"],
        })

    return {
        "images": uploaded,
        "urls":   [img["url"] for img in uploaded],
        "count":  len(uploaded),
    }

    
@product_router.get("/{id}/reviews")
async def get_product_reviews(
    id:      str,
    skip:    int = Query(0,  ge=0),
    limit:   int = Query(10, ge=1, le=50),
    session: Session = Depends(get_db),
):
    """Public — no auth required."""
    try:
        uid = UUID(id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid product ID")
 
    from sqlalchemy import func
    from app.rating.models import Rating

    def _attach_ratings(products, session):
        if not products:
            return
        ids = [p.id for p in products]
        rows = (session.query(Rating.product_id,
                              func.avg(Rating.rating).label("avg"),
                              func.count(Rating.id).label("cnt"))
                .filter(Rating.product_id.in_(ids))
                .group_by(Rating.product_id).all())
        agg = {r.product_id: (float(r.avg or 0), int(r.cnt or 0)) for r in rows}
        for p in products:
            avg, cnt = agg.get(p.id, (0.0, 0))
            p.average_rating, p.review_count = round(avg, 2), cnt
 
    base_q = session.query(Rating).filter(
        Rating.product_id == uid,
    )
 
    total   = base_q.count()
    reviews = (
        base_q
        .order_by(Rating.id.desc())   # no created_at column — use id
        .offset(skip)
        .limit(limit)
        .all()
    )
 
    # Distribution using 'rating' field (1–5)
    dist_rows = (
        session.query(Rating.rating, func.count(Rating.id))
        .filter(Rating.product_id == uid)
        .group_by(Rating.rating)
        .all()
    )
    dist = {int(r[0]): int(r[1]) for r in dist_rows}
    avg  = (sum(int(r[0]) * int(r[1]) for r in dist_rows) / total) if total > 0 else 0.0
 
    # Get customer name from the related Users object
    def get_name(r):
        try:
            u = r.user
            if u:
                return getattr(u, 'name', None) or getattr(u, 'username', None) or getattr(u, 'email', None) or 'Customer'
        except Exception:
            pass
        return 'Customer'
 
    return {
        "total":   total,
        "average": round(avg, 1),
        "distribution": {
            "5": dist.get(5, 0), "4": dist.get(4, 0), "3": dist.get(3, 0),
            "2": dist.get(2, 0), "1": dist.get(1, 0),
        },
        "reviews": [
            {
                "id":            str(r.id),
                "customer_name": get_name(r),
                "avatar":        None,
                "rating":        r.rating,
                "comment":       r.comment or "",                       # column is 'comment'
                "images":        [],
                "is_verified":   bool(getattr(r, "is_approved", True)),
                "created_at":    r.created_at.isoformat() if r.created_at else None,  # column exists
            }
            for r in reviews
        ],
    }
 
 
@product_router.post("/{id}/reviews", status_code=201)
async def submit_product_review(
    id:      str,
    stars:   int  = Form(..., ge=1, le=5),
    comment: str  = Form(...),
    images:  List[UploadFile]           = File(default=[]),  # optional, default empty list
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    """Authenticated — submit a review."""
    from app.rating.models import Rating
 
    if not comment or len(comment.strip()) < 3:
        raise HTTPException(status_code=400, detail="Review too short (min 3 chars)")
 
    try:
        uid = UUID(id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid product ID")
 
    product = session.query(Product).filter(Product.id == uid).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
 
    # Get user_id from JWT payload
    user_id_raw = user.get("id") or user.get("user_id") or user.get("sub")
    if not user_id_raw:
        raise HTTPException(status_code=401, detail="Could not identify user from token")
 
    try:
        user_uid = UUID(str(user_id_raw))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID in token")
 
    review = Rating(
        product_id = uid,
        user_id    = user_uid,
        rating     = stars,      # your model uses 'rating' not 'stars'
        comment    = comment.strip(),  # your model uses 'review' not 'comment'
    )
 
    session.add(review)
    session.commit()
 
    return {
        "message": "Review submitted successfully!",
        "id":      str(review.id),
    }
 