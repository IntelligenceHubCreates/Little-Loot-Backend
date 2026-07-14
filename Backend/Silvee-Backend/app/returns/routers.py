# app/returns/routers.py
"""
Return subsystem — HTTP layer (Phase 13, Stage 3b).

Thin endpoints: validate auth, delegate to services.py (which owns eligibility,
the state machine, and all money/stock side-effects + commits its own txn).

Two routers:
  returns_router        — customer  (prefix /api/returns,        JWT, own data only)
  admin_returns_router  — admin     (prefix /api/admin/returns,  JWT role=1)

Mount in main.py:
    from app.returns.routers import returns_router, admin_returns_router
    server.include_router(returns_router)
    server.include_router(admin_returns_router)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.users.utils import JWTBearer
from app.returns import services
from app.returns.schemas import (
    ReturnCreateRequest, CancelRequest,
    StatusUpdateRequest, ApproveRequest, RejectRequest,
    ReceiveRequest, RefundRequest, ReplacementRequest, AdminNotesRequest,RefundActionIn,
)
import json, hmac, hashlib
from datetime import datetime, timezone
from fastapi import Request
from app.returns.models import Refund
from app.settings import settings


# ── Auth helpers (self-contained; no coupling to admin/routers.py) ──

def _require_user(user) -> str:
    """Return the caller's user id, or 401. (JWTBearer normally raises on a
    missing/invalid token; this guards the authenticated-but-malformed case.)"""
    if not user or not user.get("id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user["id"]


def _require_admin(user) -> str:
    if not user or user.get("role") != 1:
        raise HTTPException(status_code=401, detail="Unauthorised")
    return user["id"]


# ═══════════════════════════════════════════════════════════════════
# CUSTOMER  —  /api/returns
# ═══════════════════════════════════════════════════════════════════

returns_router = APIRouter(prefix="/api/returns", tags=["Returns"])


@returns_router.post("", status_code=201)
async def create_return(
    payload: ReturnCreateRequest,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    """Create an item-level return request for one of the caller's delivered
    orders. Eligibility (delivered, within window, qty-cap) enforced server-side."""
    uid = _require_user(user)
    return services.create_return_request(session, uid, payload)


@returns_router.get("/my")
async def my_returns(
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    uid = _require_user(user)
    return services.list_my_returns(session, uid)


@returns_router.get("/my/{return_id}")
async def my_return_detail(
    return_id: str,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    uid = _require_user(user)
    return services.get_my_return(session, uid, return_id)


@returns_router.post("/{return_id}/cancel")
async def cancel_my_return(
    return_id: str,
    payload: CancelRequest = CancelRequest(),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    uid = _require_user(user)
    return services.cancel_return(session, uid, return_id, note=payload.note)


@returns_router.post("/{return_id}/proof", status_code=201)
async def upload_return_proof(
    return_id: str,
    file: UploadFile = File(...),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    """Upload an image/video proof to one of the caller's returns (Cloudinary).
    Reuses the exact upload pattern from admin upload-logo / user avatar."""
    uid = _require_user(user)

    ctype = file.content_type or ""
    if not (ctype.startswith("image/") or ctype.startswith("video/")):
        raise HTTPException(status_code=400, detail="Proof must be an image or video file")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(contents) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File exceeds 15 MB")

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
            contents, folder="littleloot/returns", resource_type="auto",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cloudinary upload failed: {exc}")

    url       = result.get("secure_url", "")
    public_id = result.get("public_id")
    file_type = result.get("resource_type", "image")  # "image" | "video"

    return services.add_proof_url(
        session, return_id=return_id, user_id=uid,
        file_url=url, public_id=public_id, file_type=file_type, is_admin=False,
    )


# ═══════════════════════════════════════════════════════════════════
# ADMIN  —  /api/admin/returns
# ═══════════════════════════════════════════════════════════════════

admin_returns_router = APIRouter(prefix="/api/admin/returns", tags=["Admin Returns"])


@admin_returns_router.get("")
async def admin_list_returns(
    skip:      int           = Query(0,  ge=0),
    limit:     int           = Query(20, ge=1, le=100),
    status:    Optional[str] = Query(None, description="Filter by return status"),
    reason:    Optional[str] = Query(None, description="Filter by reason"),
    search:    Optional[str] = Query(None, description="Order id / return id / customer / product"),
    date_from: Optional[str] = Query(None, description="ISO date (created_at >=)"),
    date_to:   Optional[str] = Query(None, description="ISO date (created_at <=)"),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    return services.list_returns_admin(
        session, skip=skip, limit=limit, status=status, reason=reason,
        search=search, date_from=date_from, date_to=date_to,
    )


@admin_returns_router.get("/{return_id}")
async def admin_return_detail(
    return_id: str,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    _require_admin(user)
    return services.get_return_admin(session, return_id)


@admin_returns_router.put("/{return_id}/status")
async def admin_update_status(
    return_id: str,
    payload: StatusUpdateRequest,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    """Generic status move — restricted to no-side-effect transitions
    (under_review, pickup_scheduled, picked_up, completed). Approve/reject/
    receive/refund/replacement have dedicated endpoints."""
    admin_id = _require_admin(user)
    return services.admin_set_status(session, admin_id, return_id, payload.status, note=payload.note)


@admin_returns_router.put("/{return_id}/approve")
async def admin_approve(
    return_id: str,
    payload: ApproveRequest = ApproveRequest(),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    admin_id = _require_admin(user)
    return services.approve_return(session, admin_id, return_id,
                                   admin_notes=payload.admin_notes, note=payload.note)


@admin_returns_router.put("/{return_id}/reject")
async def admin_reject(
    return_id: str,
    payload: RejectRequest,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    admin_id = _require_admin(user)
    return services.reject_return(session, admin_id, return_id,
                                  rejection_reason=payload.rejection_reason,
                                  admin_notes=payload.admin_notes, note=payload.note)


@admin_returns_router.put("/{return_id}/receive")
async def admin_receive(
    return_id: str,
    payload: ReceiveRequest,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    """Mark received + set per-item condition. Resellable units restock here."""
    admin_id = _require_admin(user)
    return services.receive_return(session, admin_id, return_id, payload.items, note=payload.note)


@admin_returns_router.put("/{return_id}/refund")
def admin_refund(return_id: str, body: RefundActionIn,
                 session: Session = Depends(get_db),
                 user=Depends(JWTBearer())):
    admin_id = _require_admin(user)                      # ← add the role check (was missing too)
    return services.process_refund(                      # ← services, not svc
        session, admin_id, return_id,
        amount=body.amount, method=body.method, status=body.status,
        transaction_reference=body.transaction_reference, note=body.note,
        execute_gateway=body.execute_gateway, speed=body.speed,
    )


@admin_returns_router.put("/{return_id}/replacement")
async def admin_replacement(
    return_id: str,
    payload: ReplacementRequest = ReplacementRequest(),
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    """Create/track a replacement shipment (idempotent, 1:1). Stock-checked and
    deducted once on first dispatch/delivered."""
    admin_id = _require_admin(user)
    return services.dispatch_replacement(
        session, admin_id, return_id,
        product_id=payload.product_id, quantity=payload.quantity,
        tracking_number=payload.tracking_number, status=payload.status, note=payload.note,
    )


@admin_returns_router.put("/{return_id}/notes")
async def admin_set_notes(
    return_id: str,
    payload: AdminNotesRequest,
    user=Depends(JWTBearer()),
    session: Session = Depends(get_db),
):
    """Update internal admin notes without changing status."""
    _require_admin(user)
    return services.set_admin_notes(session, return_id, payload.admin_notes)

@returns_router.post("/webhooks/razorpay-refund")
async def razorpay_refund_webhook(request: Request, session: Session = Depends(get_db)):
    """
    Razorpay refund.* webhook. Verifies the signature over the RAW body, then
    updates the gateway_status of a refund WE ALREADY INITIATED. It never calls
    the gateway and never creates a refund — so an inbound webhook can't move money.
    """
    raw = await request.body()
    secret = settings.razorpay_webhook_secret
    if not secret:
        # Not configured — accept-and-ignore so Razorpay doesn't hammer retries.
        return {"status": "ignored: webhook secret not configured"}

    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, request.headers.get("X-Razorpay-Signature", "")):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    payload = json.loads(raw or b"{}")
    if payload.get("event") not in ("refund.processed", "refund.failed"):
        return {"status": "ignored"}

    entity   = payload.get("payload", {}).get("refund", {}).get("entity", {})
    rfnd_id  = entity.get("id")
    gw_status = entity.get("status")   # processed | failed
    if not rfnd_id:
        return {"status": "ignored: no refund id"}

    refund = session.query(Refund).filter(Refund.gateway_refund_id == rfnd_id).first()
    if not refund:
        return {"status": "ignored: unknown refund"}   # NEVER create one from a webhook

    if refund.gateway_status == gw_status:              # dedupe (at-least-once delivery)
        return {"status": "ok: already applied"}

    refund.gateway_status = gw_status
    if gw_status == "processed":
        refund.status = "completed"
        if refund.processed_at is None:
            refund.processed_at = datetime.now(timezone.utc)
    elif gw_status == "failed":
        refund.status = "failed"
    session.commit()
    return {"status": "ok"}