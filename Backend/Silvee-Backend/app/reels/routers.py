# app/reels/routers.py
"""
Instagram reels.

  GET    /api/reels?home=true            Reel Wall (active, show_on_home)
  GET    /api/reels?product_id=<id>      reels linked to a product
  GET    /api/admin/reels                admin list
  POST   /api/admin/reels                add (paste a reel/post link)
  PUT    /api/admin/reels/{id}           edit caption/cover/products/flags/order
  DELETE /api/admin/reels/{id}

Reels are embedded with Instagram's public embed (no API token needed).
"""
import re
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.products.models import Product
from app.reels.models import InstagramReel
from app.users.utils import JWTBearer

reels_router = APIRouter(prefix="/api/reels", tags=["Instagram reels"])
admin_reels_router = APIRouter(prefix="/api/admin/reels", tags=["Admin - Instagram reels"])

_LINK_RE = re.compile(r"instagram\.com/(?:[A-Za-z0-9_.]+/)?(reels?|p|tv)/([A-Za-z0-9_-]{5,40})", re.I)


def parse_instagram_link(url: str):
    """'https://www.instagram.com/reel/C9x…/?igsh=…' → ('reel', 'C9x…'). Raises 400 if not a post/reel link."""
    m = _LINK_RE.search(url or "")
    if not m:
        raise HTTPException(status_code=400, detail="Paste a link to an Instagram reel or post, e.g. https://www.instagram.com/reel/ABC123/")
    kind = m.group(1).lower()
    kind = "reel" if kind in ("reel", "reels") else kind
    return kind, m.group(2)


def _require_admin(user):
    if not user or user.get("role") != 1:
        raise HTTPException(status_code=401, detail="Unauthorised")


def _product_cards(session: Session, ids) -> dict:
    """id → small product card (only active products) for 'Shop this'."""
    ids = [i for i in (ids or []) if i]
    if not ids:
        return {}
    out = {}
    for p in session.query(Product).filter(Product.id.in_(ids), Product.is_active.is_(True)).all():
        img = None
        pi = p.product_image or []
        if pi:
            f = pi[0]
            img = f.get("url") if isinstance(f, dict) else (f if isinstance(f, str) else None)
        orig = float(p.original_price or 0)
        amt = float(p.amount_discount or 0)
        pct = float(p.percentage_discount or 0)
        price = orig - amt if amt > 0 else (round(orig - orig * pct / 100) if pct > 0 else orig)
        out[p.id] = {"id": str(p.id), "name": p.name, "sku": getattr(p, "sku", None), "image": img, "price": max(0.0, price)}
    return out


def _serialize(r: InstagramReel, cards: dict, admin: bool = False) -> dict:
    d = {
        "id": str(r.id),
        "url": r.url,
        "shortcode": r.shortcode,
        "kind": r.kind,
        "embed_url": f"https://www.instagram.com/{r.kind}/{r.shortcode}/embed/",
        "caption": r.caption,
        "cover_url": r.cover_url,
        "video_url": r.video_url,
        "products": [cards[pid] for pid in (r.product_ids or []) if pid in cards],
    }
    if admin:
        d.update({
            "product_ids": [str(p) for p in (r.product_ids or [])],
            "show_on_home": r.show_on_home, "is_active": r.is_active, "sort_order": r.sort_order,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return d


# ── Public ────────────────────────────────────────────────────────

@reels_router.get("")
def list_reels(
    home: bool = Query(False),
    product_id: Optional[str] = Query(None),
    limit: int = Query(12, ge=1, le=30),
    session: Session = Depends(get_db),
):
    q = session.query(InstagramReel).filter(InstagramReel.is_active.is_(True))
    if product_id:
        try:
            pid = uuid.UUID(product_id)
        except ValueError:
            return []
        q = q.filter(InstagramReel.product_ids.any(pid))
    elif home:
        q = q.filter(InstagramReel.show_on_home.is_(True))
    rows = q.order_by(InstagramReel.sort_order.asc(), InstagramReel.created_at.desc()).limit(limit).all()
    cards = _product_cards(session, {pid for r in rows for pid in (r.product_ids or [])})
    return [_serialize(r, cards) for r in rows]


# ── Admin ─────────────────────────────────────────────────────────

class ReelIn(BaseModel):
    url: str = Field(..., max_length=500)
    caption: Optional[str] = Field(None, max_length=200)
    cover_url: Optional[str] = Field(None, max_length=1000)
    video_url: Optional[str] = Field(None, max_length=1000)
    product_ids: List[str] = Field(default_factory=list, max_length=20)
    show_on_home: bool = True
    is_active: bool = True
    sort_order: int = Field(0, ge=-1000, le=1000)


class ReelUpdate(BaseModel):
    url: Optional[str] = Field(None, max_length=500)
    caption: Optional[str] = Field(None, max_length=200)
    cover_url: Optional[str] = Field(None, max_length=1000)
    video_url: Optional[str] = Field(None, max_length=1000)
    product_ids: Optional[List[str]] = Field(None, max_length=20)
    show_on_home: Optional[bool] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = Field(None, ge=-1000, le=1000)


def _clean_product_ids(session: Session, ids: List[str]):
    out = []
    for raw in ids or []:
        try:
            u = uuid.UUID(str(raw))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid product id: {raw}")
        if u not in out:
            out.append(u)
    if out:
        found = {p.id for p in session.query(Product.id).filter(Product.id.in_(out)).all()}
        missing = [str(u) for u in out if u not in found]
        if missing:
            raise HTTPException(status_code=400, detail=f"Product not found: {missing[0]}")
    return out


def _cover(url: Optional[str]) -> Optional[str]:
    url = (url or "").strip()
    if not url:
        return None
    if not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Cover image must be an https:// link")
    return url


@admin_reels_router.get("")
def admin_list_reels(session: Session = Depends(get_db), user=Depends(JWTBearer())):
    _require_admin(user)
    rows = session.query(InstagramReel).order_by(InstagramReel.sort_order.asc(), InstagramReel.created_at.desc()).all()
    cards = _product_cards(session, {pid for r in rows for pid in (r.product_ids or [])})
    return [_serialize(r, cards, admin=True) for r in rows]


@admin_reels_router.post("", status_code=201)
def admin_create_reel(body: ReelIn, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    _require_admin(user)
    kind, code = parse_instagram_link(body.url)
    if session.query(InstagramReel).filter(InstagramReel.shortcode == code).first():
        raise HTTPException(status_code=409, detail="This reel has already been added")
    r = InstagramReel(
        url=f"https://www.instagram.com/{kind}/{code}/", shortcode=code, kind=kind,
        caption=(body.caption or "").strip() or None, cover_url=_cover(body.cover_url),
        video_url=_cover(body.video_url),
        product_ids=_clean_product_ids(session, body.product_ids),
        show_on_home=body.show_on_home, is_active=body.is_active, sort_order=body.sort_order,
    )
    session.add(r)
    session.commit()
    session.refresh(r)
    return _serialize(r, _product_cards(session, r.product_ids), admin=True)


@admin_reels_router.put("/{reel_id}")
def admin_update_reel(reel_id: str, body: ReelUpdate, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    _require_admin(user)
    try:
        rid = uuid.UUID(reel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid reel id")
    r = session.query(InstagramReel).filter(InstagramReel.id == rid).first()
    if not r:
        raise HTTPException(status_code=404, detail="Reel not found")
    fields = body.model_fields_set
    if "url" in fields and body.url:
        kind, code = parse_instagram_link(body.url)
        clash = session.query(InstagramReel).filter(InstagramReel.shortcode == code, InstagramReel.id != r.id).first()
        if clash:
            raise HTTPException(status_code=409, detail="Another entry already uses this reel")
        r.kind, r.shortcode, r.url = kind, code, f"https://www.instagram.com/{kind}/{code}/"
    if "caption" in fields:
        r.caption = (body.caption or "").strip() or None
    if "cover_url" in fields:
        r.cover_url = _cover(body.cover_url)
    if "video_url" in fields:
        r.video_url = _cover(body.video_url)
    if "product_ids" in fields and body.product_ids is not None:
        r.product_ids = _clean_product_ids(session, body.product_ids)
    for f in ("show_on_home", "is_active", "sort_order"):
        if f in fields and getattr(body, f) is not None:
            setattr(r, f, getattr(body, f))
    session.commit()
    session.refresh(r)
    return _serialize(r, _product_cards(session, r.product_ids), admin=True)


@admin_reels_router.delete("/{reel_id}", status_code=204)
def admin_delete_reel(reel_id: str, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    _require_admin(user)
    try:
        rid = uuid.UUID(reel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid reel id")
    r = session.query(InstagramReel).filter(InstagramReel.id == rid).first()
    if r:
        session.delete(r)
        session.commit()
