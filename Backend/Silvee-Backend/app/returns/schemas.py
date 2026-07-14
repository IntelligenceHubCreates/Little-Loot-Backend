# app/returns/schemas.py
"""
Return subsystem — REQUEST validation only (Phase 13, Stage 3a).

Response shapes are produced by hand-built serializers in services.py
(matching the existing _serialize_order / _serialize_admin_order pattern),
so these models cover inbound payloads exclusively.

Pydantic v2 (the runtime warns about V2 config keys), so we use field_validator.
"""
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.returns.models import (
    REQUEST_TYPES, RETURN_REASONS, REFUND_STATUSES, REPLACEMENT_STATUSES,
)

# Transitions an admin may set via the PLAIN status endpoint. Side-effecting
# targets (received / refunded / replacement_dispatched) are intentionally
# EXCLUDED — they must go through their dedicated endpoints so stock/refund
# logic fires. approved/rejected likewise have dedicated endpoints.
GENERIC_STATUS_TARGETS = ("under_review", "pickup_scheduled", "picked_up", "completed")


# ── Customer ──────────────────────────────────────────────────────

class ReturnItemInput(BaseModel):
    order_item_id: str
    quantity: int = Field(..., ge=1)

class RefundActionIn(BaseModel):
    amount: Optional[float] = None
    method: str = "manual"
    status: str = "completed"
    transaction_reference: Optional[str] = None
    note: Optional[str] = None
    execute_gateway: bool = False   # NEW — the admin's explicit "move money now" permission
    speed: str = "normal"           # NEW — normal | optimum


class ReturnCreateRequest(BaseModel):
    order_id: str
    request_type: str
    reason: str
    description: Optional[str] = None
    items: List[ReturnItemInput]

    @field_validator("request_type")
    @classmethod
    def _v_type(cls, v: str) -> str:
        if v not in REQUEST_TYPES:
            raise ValueError(f"request_type must be one of {REQUEST_TYPES}")
        return v

    @field_validator("reason")
    @classmethod
    def _v_reason(cls, v: str) -> str:
        if v not in RETURN_REASONS:
            raise ValueError(f"reason must be one of {RETURN_REASONS}")
        return v

    @field_validator("items")
    @classmethod
    def _v_items(cls, v):
        if not v:
            raise ValueError("At least one item is required")
        return v


class CancelRequest(BaseModel):
    note: Optional[str] = None


# ── Admin ─────────────────────────────────────────────────────────

class StatusUpdateRequest(BaseModel):
    status: str
    note: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _v_status(cls, v: str) -> str:
        if v not in GENERIC_STATUS_TARGETS:
            raise ValueError(
                f"status must be one of {GENERIC_STATUS_TARGETS}. Use the "
                f"approve / reject / receive / refund / replacement endpoints "
                f"for those transitions."
            )
        return v


class ApproveRequest(BaseModel):
    admin_notes: Optional[str] = None
    note: Optional[str] = None


class RejectRequest(BaseModel):
    rejection_reason: str = Field(..., min_length=1)
    admin_notes: Optional[str] = None
    note: Optional[str] = None


class ReceiveItemInput(BaseModel):
    return_item_id: str
    condition_status: str                     # "resellable" | "damaged"
    restock_quantity: Optional[int] = None    # defaults: qty if resellable else 0

    @field_validator("condition_status")
    @classmethod
    def _v_cond(cls, v: str) -> str:
        if v not in ("resellable", "damaged"):
            raise ValueError("condition_status must be 'resellable' or 'damaged'")
        return v


class ReceiveRequest(BaseModel):
    items: List[ReceiveItemInput]
    note: Optional[str] = None

    @field_validator("items")
    @classmethod
    def _v_items(cls, v):
        if not v:
            raise ValueError("At least one item condition is required")
        return v


class RefundRequest(BaseModel):
    amount: Optional[float] = None            # defaults to sum(item_price * qty)
    method: str = "manual"                    # manual | razorpay | upi | bank_transfer | store_credit
    status: str = "completed"                 # pending | initiated | completed | failed
    transaction_reference: Optional[str] = None
    note: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _v_status(cls, v: str) -> str:
        if v not in REFUND_STATUSES:
            raise ValueError(f"status must be one of {REFUND_STATUSES}")
        return v


class ReplacementRequest(BaseModel):
    product_id: Optional[str] = None          # defaults to first returned item's product
    quantity: Optional[int] = None            # defaults to total returned units
    tracking_number: Optional[str] = None
    status: str = "dispatched"                # pending | dispatched | delivered
    note: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _v_status(cls, v: str) -> str:
        if v not in REPLACEMENT_STATUSES:
            raise ValueError(f"status must be one of {REPLACEMENT_STATUSES}")
        return v


class AdminNotesRequest(BaseModel):
    admin_notes: str = ""