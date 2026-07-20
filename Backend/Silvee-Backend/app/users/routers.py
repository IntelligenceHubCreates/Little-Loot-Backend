import os
import hashlib
import secrets
import logging
from datetime import datetime, timedelta, timezone

import resend
from app.email.service import send_welcome_email
from app.users.services import get_hashed_password, verify_password, handle_google_login
from fastapi import APIRouter, Depends, HTTPException, Response, status, Request
from sqlalchemy.orm import Session
from app import models
from app.db import SessionLocal, get_db
from app.limiter import limiter
from app.users.models import UserTokens, Users, PasswordResetToken
from app.users.schemas import (
    TokenSchema, UserCreate, requestdetails, GoogleLoginRequest,
    ForgotPasswordRequest, ResetPasswordRequest,
)
from app.users.utils import COOKIE_ACCESS_KEY, create_access_token, get_current_user
from app.settings import settings
import httpx
import json
from app.users.utils import JWTBearer

logger = logging.getLogger(__name__)
IS_PRODUCTION = os.getenv("ENVIRONMENT", "development").lower() == "production"

router = APIRouter(prefix='/api/user')


@router.post("/register")
@limiter.limit("10/minute")
def register_user(request: Request, user: UserCreate, response: Response, session: Session = Depends(get_db)):
    existing_user = session.query(Users).filter_by(email=user.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    encrypted_password = get_hashed_password(user.password)

    new_user = Users(
        name=user.name,
        phone=user.phone,
        email=user.email,
        hashed_password=encrypted_password,
        confirmed=True,
        role=5,
    )

    session.add(new_user)
    session.commit()
    session.refresh(new_user)

    # ── Welcome email (fire-and-forget) ──────────────────────────────────────
    send_welcome_email(user_email=new_user.email, user_name=new_user.name or "")

    access = create_access_token(new_user.id, session)

    response.set_cookie(
        key=COOKIE_ACCESS_KEY,
        value=access,
        httponly=True,
        samesite="strict",
        secure=IS_PRODUCTION,  # SECURITY: HTTPS-only in production
    )

    return {"message": "user created successfully", "token": access}


@router.post('/login')
@limiter.limit("5/minute")
def login(request: Request, body: requestdetails, response: Response, db: Session = Depends(get_db)):
    # SECURITY: Use a single generic error for both wrong-email and wrong-password.
    # Separate messages ("Incorrect email" vs "Incorrect password") allow an attacker
    # to enumerate which email addresses are registered in the system.
    _BAD_CREDS = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid email or password",
    )
    user = db.query(Users).filter(Users.email == body.email).first()
    if user is None:
        raise _BAD_CREDS

    hashed_pass = user.hashed_password
    if not hashed_pass or not verify_password(body.password, hashed_pass):
        raise _BAD_CREDS

    access = create_access_token(user.id, db)

    response.set_cookie(
        key=COOKIE_ACCESS_KEY,
        value=access,
        httponly=True,
        samesite="strict",
        secure=IS_PRODUCTION,  # SECURITY: HTTPS-only in production
    )

    # SECURITY: Return only safe fields — never the SQLAlchemy ORM object which
    # includes hashed_password, google_id_token, google_access_token etc.
    return {
        "user": {
            "id":    str(user.id),
            "email": user.email,
            "name":  user.name or "",
            "role":  user.role,
        },
        "token":   access,
        "Message": "Successfully Logged In",
    }


@router.post('/google-login')
@limiter.limit("10/minute")
async def google_login(request: Request, google_data: GoogleLoginRequest, response: Response, db: Session = Depends(get_db)):
    # SECURITY: verify the Google token before trusting any identity claim.
    # Without this check, anyone who knows a victim's email can pass arbitrary
    # google_id values and receive a valid JWT for the victim's account.
    access_token = google_data.google_access_token or ""
    id_token     = google_data.google_id_token     or ""

    if not access_token and not id_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google login failed: no token provided"
        )

    verified_profile = None
    if access_token:
        verified_profile = await verify_google_token(access_token)

    if not verified_profile:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google login failed: token verification failed"
        )

    # Use verified data from Google — never trust the request body for identity
    google_data.google_id = verified_profile.get("id",   google_data.google_id)
    google_data.email     = verified_profile.get("email",google_data.email)
    google_data.name      = verified_profile.get("name", google_data.name)
    google_data.image     = verified_profile.get("picture", google_data.image)

    try:
        user = handle_google_login(db, google_data)
        access = create_access_token(user.id, db)

        response.delete_cookie(key=COOKIE_ACCESS_KEY, httponly=True, samesite="strict")
        response.set_cookie(
            key=COOKIE_ACCESS_KEY,
            value=access,
            httponly=True,
            samesite="strict",
            secure=IS_PRODUCTION,
        )

        return {
            "message": "Successfully logged in with Google",
            "token": access,
            "user": {
                "id":            str(user.id),
                "email":         user.email,
                "name":          user.name,
                "phone":         user.phone or "",
                "profile_image": user.profile_image or "",
                "confirmed":     user.confirmed,
                "role":          user.role,
            },
        }
    except HTTPException:
        raise
    except Exception:
        # SECURITY: Never return raw exception text — it can leak DB details.
        logger.warning("Google login failed", exc_info=False)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google login failed")


async def verify_google_token(access_token: str) -> dict:
    """Verify Google access token with Google API."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            if response.status_code == 200:
                return response.json()
            return None
    except Exception:
        logger.warning("Google token verification failed", exc_info=False)
        return None


@router.get('/verify-login')
async def verify_login(request: Request, db: Session = Depends(get_db)):
    """Verify if user is still logged in."""
    try:
        user, error_message = await get_current_user(request, db)
        if not user:
            return {
                "is_logged_in": False,
                "message": error_message or "User not authenticated"
            }

        is_google_user = user.get("google_id") is not None

        return {
            "is_logged_in": True,
            "user": {
                "id":            user.get("id"),
                "email":         user.get("email", ''),
                "name":          user.get("name", '') or '',
                "phone":         user.get("phone", '') or '',
                "profile_image": user.get("profile_image", '') or '',
                "role":          user.get("role", 5),
                "is_google_user": is_google_user,
            },
            "message": "User is authenticated",
        }

    except Exception:
        logger.warning("verify_login error", exc_info=False)
        return {
            "is_logged_in": False,
            "message": "Authentication error",
        }


# NOTE: The previous GET '/profile' handler that lived here has been REMOVED.
# It referenced a non-existent `decode_token` import (crashed on every Bearer
# request) and duplicated the route now owned by app/users/profile_router.py,
# which returns the full profile (dob, gender, profile_picture) and is
# header-aware via the fixed get_current_user.


@router.post('/logout')
async def logout(response: Response):
    """Clear access token cookie."""
    response.delete_cookie(
        key=COOKIE_ACCESS_KEY,
        httponly=True,
        samesite="strict",
        secure=IS_PRODUCTION,
    )
    return {"message": "Successfully logged out"}


@router.post('/forgot-password')
@limiter.limit("3/minute")
def forgot_password(request: Request, payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Sends a password-reset link to the given email.
    Always returns 200 regardless of whether the email exists (prevents enumeration).
    """
    _OK = {"message": "If that email is registered, a reset link has been sent."}

    user = db.query(Users).filter(Users.email == payload.email).first()
    if not user:
        return _OK

    # Delete any existing tokens for this user before issuing a new one
    db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).delete()
    db.commit()

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    db.add(PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
    ))
    db.commit()

    reset_link = f"{settings.frontend_url}/reset-password?token={raw_token}"

    if not settings.resend_api_key:
        logger.warning("RESEND_API_KEY not configured — reset link not sent: %s", reset_link)
        return _OK

    try:
        resend.api_key = settings.resend_api_key
        resend.Emails.send({
            "from": f"Little Loot <{settings.resend_from_email}>",
            "to": [user.email],
            "subject": "Reset your Little Loot password",
            "html": f"""
            <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px 24px;background:#f7f8fc;border-radius:16px">
              <div style="text-align:center;margin-bottom:24px">
                <span style="font-size:32px">🌟</span>
                <h1 style="font-size:22px;font-weight:800;color:#0f1f45;margin:8px 0 4px">Little Loot</h1>
              </div>
              <div style="background:#fff;border-radius:12px;padding:28px 24px">
                <h2 style="font-size:18px;color:#0f1f45;margin:0 0 12px">Password Reset Request</h2>
                <p style="color:#64748b;font-size:14px;line-height:1.6;margin:0 0 20px">
                  Hi {user.name or 'there'},<br><br>
                  We received a request to reset the password for your account.
                  Click the button below to set a new password. This link expires in <strong>1 hour</strong>.
                </p>
                <a href="{reset_link}"
                   style="display:block;text-align:center;background:linear-gradient(135deg,#ff6b5b,#ff4d3a);color:#fff;font-weight:700;font-size:15px;padding:14px 24px;border-radius:10px;text-decoration:none;margin-bottom:20px">
                  Reset My Password →
                </a>
                <p style="color:#94a3b8;font-size:12px;line-height:1.5;margin:0">
                  If you didn't request this, you can safely ignore this email.
                  Your password won't change until you click the link above.<br><br>
                  <strong>Never share this link with anyone.</strong>
                </p>
              </div>
              <p style="text-align:center;color:#cbd5e1;font-size:11px;margin-top:20px">
                © Little Loot — gifts that spark joy
              </p>
            </div>
            """,
        })
    except Exception:
        logger.error("Failed to send password reset email", exc_info=True)

    return _OK


@router.post('/reset-password')
@limiter.limit("5/minute")
def reset_password(request: Request, payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Validates the reset token and updates the user's password."""
    INVALID = HTTPException(status_code=400, detail="This reset link is invalid or has expired.")

    if not payload.token or len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
    record = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == token_hash
    ).first()

    if not record:
        raise INVALID

    if record.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        db.delete(record)
        db.commit()
        raise INVALID

    user = db.query(Users).filter(Users.id == record.user_id).first()
    if not user:
        db.delete(record)
        db.commit()
        raise INVALID

    user.hashed_password = get_hashed_password(payload.new_password)
    db.delete(record)
    db.commit()

    return {"message": "Password updated successfully. You can now sign in."}


@router.get('/admin/customers')
def admin_get_customers(
    skip: int = 0,
    limit: int = 15,
    segment: str = None,
    search: str = None,
    db: Session = Depends(get_db),
    user=Depends(JWTBearer())
):
    """Paginated customer list (admin only). Returns {data, totalCount}."""
    if user is None or user['role'] != 1:
        raise HTTPException(status_code=401, detail="Unauthorised")

    from sqlalchemy import func as sqlfunc
    from app.orders.models import Order

    query = db.query(
        Users,
        sqlfunc.count(Order.id).label("orders_count"),
        sqlfunc.coalesce(sqlfunc.sum(Order.total_amount), 0).label("total_spent"),
        sqlfunc.max(Order.created_at).label("last_order"),
    ).outerjoin(Order, Users.id == Order.user_id)\
     .filter(Users.role != 1)\
     .group_by(Users.id)

    if search:
        like = f"%{search}%"
        query = query.filter(
            (Users.name.ilike(like)) | (Users.email.ilike(like)) | (Users.phone.ilike(like))
        )

    # Segment filter (mirrors the segment_of logic below)
    if segment and segment.lower() in ("vip", "regular", "new"):
        seg = segment.lower()
        having = sqlfunc.count(Order.id)
        spent  = sqlfunc.coalesce(sqlfunc.sum(Order.total_amount), 0)
        if seg == "vip":
            query = query.having((spent >= 10000) | (having >= 10))
        elif seg == "regular":
            query = query.having((having >= 3) & (having < 10) & (spent < 10000))
        else:  # new
            query = query.having(having < 3)

    # Count distinct customers (subquery so GROUP BY/HAVING are respected)
    total = query.count()

    rows = query.order_by(Users.created_at.desc()).offset(skip).limit(limit).all()

    def segment_of(orders_count, total_spent):
        if float(total_spent) >= 10000 or orders_count >= 10:
            return "vip"
        if orders_count >= 3:
            return "regular"
        return "new"

    return {
        "data": [
            {
                "id":           str(u.id),
                "name":         u.name or "",
                "email":        u.email,
                "phone":        u.phone or "",
                "city":         "",                       # lives on UserAddress; see detail endpoint
                "total_orders": int(oc),
                "total_spent":  float(ts),
                "last_order_date": last.isoformat() if last else None,
                "segment":      segment_of(oc, ts),
                "role":         u.role,
                "is_active":    bool(getattr(u, "is_active", True)),
                "created_at":   u.created_at.isoformat(),
            }
            for u, oc, ts, last in rows
        ],
        "totalCount": total,
    }

@router.get('/admin/customers/{customer_id}')
def admin_get_customer_detail(
    customer_id: str,
    db: Session = Depends(get_db),
    user=Depends(JWTBearer())
):
    """Full customer profile: aggregates + recent orders (by user_id) + addresses."""
    if user is None or user['role'] != 1:
        raise HTTPException(status_code=401, detail="Unauthorised")

    from uuid import UUID
    from sqlalchemy import func as sqlfunc
    from app.orders.models import Order
    from app.users.models import UserAddress

    try:
        uid = UUID(customer_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid customer ID")

    u = db.query(Users).filter(Users.id == uid).first()
    if not u:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Aggregates (true totals, independent of the 50-row cap below)
    agg = db.query(
        sqlfunc.count(Order.id),
        sqlfunc.coalesce(sqlfunc.sum(Order.total_amount), 0),
        sqlfunc.max(Order.created_at),
    ).filter(Order.user_id == uid).first()
    total_orders = int(agg[0] or 0)
    total_spent  = float(agg[1] or 0)
    last_order   = agg[2]

    # Recent 50 orders by user_id
    orders = (
        db.query(Order)
        .filter(Order.user_id == uid)
        .order_by(Order.created_at.desc())
        .limit(50)
        .all()
    )
    orders_out = [
        {
            "id":           str(o.id),
            "order_number": str(o.id)[:8].upper(),
            "status":       o.status,
            "total_amount": float(o.total_amount or 0),
            "created_at":   o.created_at.isoformat() if o.created_at else None,
            "item_count":   len(o.order_items or []),
        }
        for o in orders
    ]

    # Saved addresses
    addrs = db.query(UserAddress).filter(UserAddress.user_id == uid).all()
    addresses_out = [
        {
            "id":            str(a.id),
            "full_name":     a.full_name,
            "phone":         a.phone,
            "address_line1": a.address_line1,
            "address_line2": a.address_line2 or "",
            "city":          a.city,
            "state":         a.state,
            "postal_code":   a.postal_code,
            "country":       a.country,
            "address_type":  a.address_type,
            "is_default":    bool(a.is_default),
        }
        for a in addrs
    ]

    # Primary city = default address's city (or first address)
    primary_city = ""
    if addresses_out:
        default_addr = next((a for a in addresses_out if a["is_default"]), addresses_out[0])
        primary_city = default_addr["city"]

    return {
        "id":              str(u.id),
        "name":            u.name or "",
        "email":           u.email,
        "phone":           u.phone or "",
        "city":            primary_city,
        "role":            u.role,
        "confirmed":       bool(u.confirmed),
        "is_active":       bool(getattr(u, "is_active", True)),
        "created_at":      u.created_at.isoformat(),
        "profile_image":   getattr(u, "profile_picture", None) or getattr(u, "profile_image", None),
        "total_orders":    total_orders,
        "total_spent":     total_spent,
        "last_order_date": last_order.isoformat() if last_order else None,
        "orders":          orders_out,
        "addresses":       addresses_out,
    }

from pydantic import BaseModel as _PydBase

class _CustomerActivePayload(_PydBase):
    is_active: bool

@router.put('/admin/customers/{customer_id}')
def admin_set_customer_active(
    customer_id: str,
    payload: _CustomerActivePayload,
    db: Session = Depends(get_db),
    user=Depends(JWTBearer())
):
    """Toggle a customer's active flag (admin only)."""
    if user is None or user['role'] != 1:
        raise HTTPException(status_code=401, detail="Unauthorised")

    from uuid import UUID
    try:
        uid = UUID(customer_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid customer ID")

    u = db.query(Users).filter(Users.id == uid).first()
    if not u:
        raise HTTPException(status_code=404, detail="Customer not found")
    if u.role == 1:
        raise HTTPException(status_code=400, detail="Cannot change an admin's status")

    u.is_active = payload.is_active
    db.commit()
    return {"id": str(u.id), "is_active": u.is_active}