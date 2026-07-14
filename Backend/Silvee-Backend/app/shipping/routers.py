# app/shipping/routers.py
"""
Shipping & Logistics — HTTP routers (Phase 14, Stage 3).

Two routers:
  admin_shipping_router  (/api/admin/shipments, /api/admin/couriers) — admin-gated
  shipping_router        (/api/shipments, /api/orders/{id}/tracking) — customer/public

Plus the Shiprocket webhook (public, token-verified) as a GUARDED STUB: it maps a
Shiprocket status → our state and advances the shipment via the same _transition
(so order-sync + notifications fire), but it is inert until SHIPROCKET_WEBHOOK_TOKEN
is set AND Shiprocket is actually sending. No fakery: unverified calls 401; with no
token configured it accept-and-ignores so retries don't pile up.

AUTH NOTE (verify): admin endpoints use the SAME dependency as admin_returns_router.
The import + Depends(...) below MUST match that router. If your admin returns routes
use e.g. Depends(get_current_admin) or a role check, swap it here identically.

CLOUDINARY NOTE (verify): the label upload reuses the SAME helper/pattern as the
returns proof upload (littleloot/returns). Point _upload_label_file at it; if returns
inlines cloudinary.uploader.upload, mirror that call here.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import (
    APIRouter, Depends, HTTPException, Request, UploadFile, File, Form,
)
from sqlalchemy.orm import Session

from app.db import get_db
from app.users.utils import JWTBearer          # ← VERIFY: must match admin_returns_router's admin dependency
from app.shipping import services as svc
from app.shipping import schemas as sch
from app.shipping.models import Shipment, CourierPartner
from app.shipping.shiprocket_adapter import verify_webhook, SHIPROCKET_STATUS_MAP, SHIPROCKET_WEBHOOK_TOKEN

# ════════════════════════════════════════════════════════════════════
# Admin router
# ════════════════════════════════════════════════════════════════════
# VERIFY: dependencies=[Depends(JWTBearer())] must be the SAME admin-gating the
# returns admin router uses. If admin uses session.accessToken + role check via a
# get_current_admin dependency, replace JWTBearer() with that here.
# ── Auth helpers — MATCH app/returns/routers.py exactly ──
def _require_admin(user) -> str:
    if not user or user.get("role") != 1:
        raise HTTPException(status_code=401, detail="Unauthorised")
    return user["id"]


# Admin routers: NO router-level dependency. Each handler authenticates via
# Depends(JWTBearer()) and authorises via _require_admin(user) — identical to
# admin_returns_router. (Router-level JWTBearer authenticates but does NOT check
# role==1, which would leave these endpoints open to any logged-in customer.)
admin_shipping_router = APIRouter(prefix="/api/admin/shipments", tags=["Admin · Shipping"])
admin_couriers_router = APIRouter(prefix="/api/admin/couriers", tags=["Admin · Couriers"])

@admin_shipping_router.get("/summary")
def summary(session: Session = Depends(get_db), user=Depends(JWTBearer())):
    _require_admin(user)
    return svc.shipments_summary(session)

@admin_shipping_router.get("/awaiting")
def awaiting_shipment(
    skip: int = 0, limit: int = 50,
    session: Session = Depends(get_db), user=Depends(JWTBearer()),
):
    """Orders past checkout that have NO active (non-cancelled) shipment yet.
    This is the admin's 'to ship' queue (Option 1)."""
    _require_admin(user)
    return svc.list_orders_awaiting_shipment(session, skip=skip, limit=limit)


@admin_shipping_router.get("/by-orders")
def shipments_by_orders(
    ids: str = "",
    session: Session = Depends(get_db), user=Depends(JWTBearer()),
):
    """Map order_id → newest active shipment id, for a comma-separated id list.
    Lets the admin Orders page show 'Ship' vs 'View shipment' (Option 2)."""
    _require_admin(user)
    id_list = [x for x in (ids or "").split(",") if x.strip()]
    return svc.shipments_for_orders(session, id_list)


@admin_shipping_router.get("")
def list_shipments(
    skip: int = 0, limit: int = 20,
    status: Optional[str] = None, courier: Optional[str] = None,
    payment: Optional[str] = None, search: Optional[str] = None,
    city: Optional[str] = None, state: Optional[str] = None, pincode: Optional[str] = None,
    date_from: Optional[str] = None, date_to: Optional[str] = None,
    sort: str = "latest",
    session: Session = Depends(get_db), user=Depends(JWTBearer()),
):
    _require_admin(user)
    return svc.list_shipments_admin(
        session, skip=skip, limit=limit, status=status, courier=courier, payment=payment,
        search=search, city=city, state=state, pincode=pincode,
        date_from=date_from, date_to=date_to, sort=sort,
    )


@admin_shipping_router.get("/{shipment_id}")
def get_shipment(shipment_id: str, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    _require_admin(user)
    return svc.get_shipment_admin(session, shipment_id)


@admin_shipping_router.post("")
def create_shipment(body: sch.CreateShipmentIn, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    admin_id = _require_admin(user)
    return svc.create_shipment(session, admin_id, body)


@admin_shipping_router.put("/{shipment_id}/pack")
def pack(shipment_id: str, body: sch.PackIn, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    admin_id = _require_admin(user)
    return svc.pack_shipment(session, admin_id, shipment_id, body)


@admin_shipping_router.put("/{shipment_id}/assign-courier")
def assign_courier(shipment_id: str, body: sch.AssignCourierIn, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    admin_id = _require_admin(user)
    return svc.assign_courier(session, admin_id, shipment_id, body)


# Label: JSON path (URL already hosted) …
@admin_shipping_router.put("/{shipment_id}/label")
def attach_label_json(shipment_id: str, body: sch.LabelIn, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    admin_id = _require_admin(user)
    return svc.attach_label(session, admin_id, shipment_id, body)


# … or multipart upload path (admin uploads a PDF/image → Cloudinary → service).
async def _upload_label_file(file: UploadFile) -> dict:
    """Mirror the returns proof-upload pattern exactly: read bytes, configure
    Cloudinary from env, upload to 'littleloot/labels' with resource_type='auto'."""
    allowed = {"application/pdf", "image/png", "image/jpeg", "image/webp"}
    if (file.content_type or "") not in allowed:
        raise HTTPException(status_code=400, detail="Label must be a PDF or image (PNG/JPG/WebP)")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(contents) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Label exceeds 15 MB")

    import cloudinary
    import cloudinary.uploader
    from app.settings import settings as env

    cloudinary.config(
        cloud_name=env.cloudinary_cloud_name,
        api_key=env.cloudinary_api_key,
        api_secret=env.cloudinary_api_secret,
    )
    try:
        result = cloudinary.uploader.upload(
            contents, folder="littleloot/labels", resource_type="auto",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Label upload failed: {exc}")
    return {"url": result.get("secure_url", ""), "public_id": result.get("public_id")}

@admin_shipping_router.put("/{shipment_id}/label-upload")
async def attach_label_upload(shipment_id: str, file: UploadFile = File(...),
    note: Optional[str] = Form(None), session: Session = Depends(get_db), user=Depends(JWTBearer())):
    admin_id = _require_admin(user)
    up = await _upload_label_file(file)   # now async — see Fix B
    payload = sch.LabelIn(label_url=up["url"], label_public_id=up.get("public_id"),
                          file_name=file.filename, generated_by="manual", note=note)
    return svc.attach_label(session, admin_id, shipment_id, payload)


@admin_shipping_router.put("/{shipment_id}/pickup")
def schedule_pickup(shipment_id: str, body: sch.PickupIn, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    admin_id = _require_admin(user)
    return svc.schedule_pickup(session, admin_id, shipment_id, body)


@admin_shipping_router.put("/{shipment_id}/pickup-failed")
def pickup_failed(shipment_id: str, body: sch.PickupFailedIn, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    admin_id = _require_admin(user)
    return svc.mark_pickup_failed(session, admin_id, shipment_id, body)


@admin_shipping_router.put("/{shipment_id}/picked-up")
def picked_up(shipment_id: str, body: sch.CancelIn, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    admin_id = _require_admin(user)
    # reusing CancelIn for its optional `note`
    return svc.mark_picked_up(session, admin_id, shipment_id, body.note)


@admin_shipping_router.put("/{shipment_id}/status")
def set_status(shipment_id: str, body: sch.StatusIn, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    admin_id = _require_admin(user)
    return svc.set_status(session, admin_id, shipment_id, body)

@admin_shipping_router.put("/{shipment_id}/delivery-attempt")
def delivery_attempt(shipment_id: str, body: sch.DeliveryAttemptIn, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    admin_id = _require_admin(user)
    return svc.record_delivery_attempt(session, admin_id, shipment_id, body)


@admin_shipping_router.put("/{shipment_id}/rto")
def initiate_rto(shipment_id: str, body: sch.RtoInitiateIn, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    admin_id = _require_admin(user)
    return svc.initiate_rto(session, admin_id, shipment_id, body)

@admin_shipping_router.put("/{shipment_id}/rto-receive")
def receive_rto(shipment_id: str, body: sch.ReceiveRtoIn, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    admin_id = _require_admin(user)
    return svc.receive_rto(session, admin_id, shipment_id, body)

@admin_shipping_router.put("/{shipment_id}/cod")
def update_cod(shipment_id: str, body: sch.CodIn, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    admin_id = _require_admin(user)
    return svc.update_cod(session, admin_id, shipment_id, body)


@admin_shipping_router.put("/{shipment_id}/cancel")
def cancel_shipment(shipment_id: str, body: sch.CancelIn, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    admin_id = _require_admin(user)
    return svc.cancel_shipment(session, admin_id, shipment_id, body)

@admin_shipping_router.put("/{shipment_id}/notes")
def set_notes(shipment_id: str, body: sch.NotesIn, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    _require_admin(user)
    return svc.set_admin_notes(session, shipment_id, body.admin_notes)

# ════════════════════════════════════════════════════════════════════
# Courier-partner admin (CourierPartner CRUD)
# ════════════════════════════════════════════════════════════════════
admin_couriers_router = APIRouter(
    prefix="/api/admin/couriers",
    tags=["Admin · Couriers"],
    dependencies=[Depends(JWTBearer())],
)


def _serialize_courier(c: CourierPartner) -> dict:
    return {
        "id": str(c.id), "name": c.name, "service_type": c.service_type,
        "is_active": bool(c.is_active), "supports_cod": bool(c.supports_cod),
        "tracking_url_template": c.tracking_url_template,
        "created_at": c.created_at.isoformat() if c.created_at else "",
    }


@admin_couriers_router.get("")
def list_couriers(session: Session = Depends(get_db), user=Depends(JWTBearer())):
    _require_admin(user)
    rows = session.query(CourierPartner).order_by(CourierPartner.name.asc()).all()
    return {"data": [_serialize_courier(c) for c in rows]}

@admin_couriers_router.post("")
def create_courier(body: sch.CourierIn, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    _require_admin(user)
    c = CourierPartner(name=body.name, service_type=body.service_type, is_active=body.is_active,
                       supports_cod=body.supports_cod, tracking_url_template=body.tracking_url_template)
    session.add(c); session.commit(); session.refresh(c)
    return _serialize_courier(c)

@admin_couriers_router.put("/{courier_id}")
def update_courier(courier_id: str, body: sch.CourierUpdateIn, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    _require_admin(user)
    c = session.query(CourierPartner).filter(CourierPartner.id == courier_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Courier not found")
    for field in ("name", "service_type", "is_active", "supports_cod", "tracking_url_template"):
        val = getattr(body, field)
        if val is not None:
            setattr(c, field, val)
    session.commit(); session.refresh(c)
    return _serialize_courier(c)

# ════════════════════════════════════════════════════════════════════
# Customer / public router
# ════════════════════════════════════════════════════════════════════
shipping_router = APIRouter(prefix="/api", tags=["Shipping"])


from app.users.utils import JWTBearer          # add if not imported
from app.orders.services import get_order       # add if not imported

@shipping_router.get("/orders/{order_id}/tracking")
def order_tracking(
    order_id: str,
    session: Session = Depends(get_db),
    user = Depends(JWTBearer()),                 # ← 1. require a valid token
):
    """Customer-safe shipment view for the order's OWNER only.
    Returns {has_shipment: false} when no shipment exists yet."""
    # 2. Confirm this order belongs to the caller before returning anything
    owned = get_order(session, user.get("id"), order_id)
    if not owned:
        raise HTTPException(status_code=404, detail="Order not found")

    return svc.get_order_tracking(session, order_id)

@shipping_router.get("/shipments/my/{shipment_id}")
def my_shipment(shipment_id: str, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    """Authenticated: a customer's own shipment detail (ownership-checked)."""
    sh = session.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not sh:
        raise HTTPException(status_code=404, detail="Shipment not found")
    uid = str(user.get("id") if isinstance(user, dict) else getattr(user, "id", ""))
    if str(sh.user_id) != uid:
        raise HTTPException(status_code=403, detail="This shipment does not belong to you")
    return svc.serialize_tracking(session, svc._load(session, shipment_id))


# ════════════════════════════════════════════════════════════════════
# Shiprocket webhook — PUBLIC, token-verified, GUARDED STUB
# ════════════════════════════════════════════════════════════════════
# Inert until SHIPROCKET_WEBHOOK_TOKEN is set AND Shiprocket sends events. When
# active, it maps a status and advances the shipment via the SAME _transition,
# so order-sync + customer notifications fire automatically. It NEVER creates a
# shipment and NEVER moves money. Unknown AWB / unmapped status → ignored.
shiprocket_webhook_router = APIRouter(prefix="/api/shipping/webhooks", tags=["Shipping · Webhooks"])


@shiprocket_webhook_router.post("/shiprocket")
async def shiprocket_webhook(request: Request, session: Session = Depends(get_db)):
    # Not configured → accept-and-ignore (so Shiprocket retries don't pile up).
    if not SHIPROCKET_WEBHOOK_TOKEN:
        return {"status": "ignored: webhook token not configured"}

    # Shiprocket sends the configured token in 'x-api-key'.
    if not verify_webhook(request.headers.get("x-api-key")):
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    try:
        body = await request.json()
    except Exception:
        return {"status": "ignored: bad body"}

    awb = body.get("awb") or body.get("awb_code")
    sr_status = (body.get("current_status") or body.get("status") or "").upper()
    if not awb:
        return {"status": "ignored: no awb"}

    target = SHIPROCKET_STATUS_MAP.get(sr_status)
    if not target:
        return {"status": f"ignored: unmapped status '{sr_status}'"}

    sh = session.query(Shipment).filter(Shipment.awb_number == awb).first()
    if not sh:
        return {"status": "ignored: unknown awb"}   # NEVER create from a webhook

    sh = svc._load(session, sh.id)
    if sh.status == target:
        return {"status": "ok: already applied"}     # dedupe (at-least-once)

    try:
        # actor_id None = system/courier. Advances status → order-sync + notify fire.
        svc._transition(session, sh, target, actor_id=None, note=f"Shiprocket: {sr_status}")
        session.commit()
    except HTTPException as e:
        # Illegal transition for current state → log-and-ignore, don't 500 the courier.
        return {"status": f"ignored: {e.detail}"}
    return {"status": "ok"}