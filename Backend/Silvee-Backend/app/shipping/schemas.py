# app/shipping/schemas.py
"""Shipping subsystem — Pydantic v2 input schemas (Phase 14, Stage 2b).
Input-only; status/reason/condition validated here (DB columns stay VARCHAR,
matching the returns subsystem)."""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, field_validator

# Statuses reachable via the GENERIC status endpoint (transit phase only).
# pack/courier/label/pickup/picked_up/rto/cancel have dedicated endpoints.
VALID_TRANSIT_STATUSES = {"in_transit", "out_for_delivery", "delivered", "lost", "damaged_in_transit"}
VALID_ATTEMPT_STATUSES = {"attempted", "failed", "delivered"}
VALID_FAILURE_REASONS  = {
    "customer_unavailable", "incorrect_address", "phone_not_reachable",
    "customer_refused", "cod_issue", "courier_delay", "weather", "other",
}
VALID_CONDITIONS = {"resellable", "damaged"}


class ShipmentItemIn(BaseModel):
    order_item_id: str
    quantity: int


class CreateShipmentIn(BaseModel):
    order_id: str
    items: Optional[List[ShipmentItemIn]] = None   # None => all not-yet-shipped items
    ship_name: Optional[str] = None
    ship_phone: Optional[str] = None
    ship_line1: Optional[str] = None
    ship_city: Optional[str] = None
    ship_state: Optional[str] = None
    ship_pincode: Optional[str] = None


class PackIn(BaseModel):
    package_weight: Optional[float] = None
    package_length: Optional[float] = None
    package_width: Optional[float] = None
    package_height: Optional[float] = None
    note: Optional[str] = None


class AssignCourierIn(BaseModel):
    courier_partner_id: Optional[str] = None
    courier_name: str
    courier_service: Optional[str] = None
    awb_number: str
    tracking_url: Optional[str] = None          # if omitted, built from the partner template
    shipping_cost: Optional[float] = None
    expected_delivery_date: Optional[date] = None
    override: bool = False                       # allow editing AWB after delivered
    note: Optional[str] = None


class LabelIn(BaseModel):
    label_url: str                               # router uploads to Cloudinary, passes URL
    label_public_id: Optional[str] = None
    file_name: Optional[str] = None
    generated_by: Optional[str] = "manual"
    note: Optional[str] = None


class PickupIn(BaseModel):
    pickup_scheduled_at: Optional[datetime] = None
    note: Optional[str] = None


class PickupFailedIn(BaseModel):
    reason: Optional[str] = None
    note: Optional[str] = None


class StatusIn(BaseModel):
    status: str
    note: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _v(cls, v):
        if v not in VALID_TRANSIT_STATUSES:
            raise ValueError(f"status must be one of {sorted(VALID_TRANSIT_STATUSES)}")
        return v


class DeliveryAttemptIn(BaseModel):
    status: str
    failure_reason: Optional[str] = None
    courier_remarks: Optional[str] = None
    next_attempt_at: Optional[datetime] = None
    customer_contacted: bool = False
    note: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _vs(cls, v):
        if v not in VALID_ATTEMPT_STATUSES:
            raise ValueError(f"status must be one of {sorted(VALID_ATTEMPT_STATUSES)}")
        return v

    @field_validator("failure_reason")
    @classmethod
    def _vf(cls, v):
        if v and v not in VALID_FAILURE_REASONS:
            raise ValueError(f"failure_reason must be one of {sorted(VALID_FAILURE_REASONS)}")
        return v


class RtoInitiateIn(BaseModel):
    note: Optional[str] = None


class RtoItemConditionIn(BaseModel):
    shipment_item_id: str
    condition_status: str
    restock_quantity: Optional[int] = None

    @field_validator("condition_status")
    @classmethod
    def _v(cls, v):
        if v not in VALID_CONDITIONS:
            raise ValueError(f"condition_status must be one of {sorted(VALID_CONDITIONS)}")
        return v


class ReceiveRtoIn(BaseModel):
    items: List[RtoItemConditionIn] = []
    note: Optional[str] = None


class CodIn(BaseModel):
    action: str            # 'collect' | 'remit'
    reference: Optional[str] = None
    note: Optional[str] = None

    @field_validator("action")
    @classmethod
    def _v(cls, v):
        if v not in ("collect", "remit"):
            raise ValueError("action must be 'collect' or 'remit'")
        return v


class NotesIn(BaseModel):
    admin_notes: str


class CancelIn(BaseModel):
    note: Optional[str] = None

class CourierIn(BaseModel):
    name: str
    service_type: Optional[str] = None
    is_active: bool = True
    supports_cod: bool = True
    tracking_url_template: Optional[str] = None


class CourierUpdateIn(BaseModel):
    name: Optional[str] = None
    service_type: Optional[str] = None
    is_active: Optional[bool] = None
    supports_cod: Optional[bool] = None
    tracking_url_template: Optional[str] = None