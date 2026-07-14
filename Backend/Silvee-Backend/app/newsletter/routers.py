# app/newsletter/routers.py
"""
FastAPI newsletter endpoints with welcome email via Resend.

Setup:
1. pip install resend
2. Add to .env:  RESEND_API_KEY=re_xxxxxxxxxxxx
3. Add to .env:  NEWSLETTER_FROM_EMAIL=hello@littleloot.in
4. Mount in main.py:
       from app.newsletter.routers import newsletter_router
       app.include_router(newsletter_router)
"""
from __future__ import annotations

import os
import logging
from sqlalchemy import Column, String, Boolean, func, text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, EmailStr
import resend

from app.db import get_db
from app.models import Base   # adjust to your Base import path

log = logging.getLogger(__name__)

newsletter_router = APIRouter(prefix="/api/newsletter", tags=["Newsletter"])

# ── Config ────────────────────────────────────────────────────────
resend.api_key  = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL      = os.getenv("NEWSLETTER_FROM_EMAIL", "hello@littleloot.in")
FROM_NAME       = "Little Loot"
STORE_URL       = os.getenv("NEXT_PUBLIC_FRONTEND_URL", "https://littleloot.in")


# ── Model ─────────────────────────────────────────────────────────

class NewsletterSubscriber(Base):
    __tablename__ = "newsletter_subscribers"

    id         = Column(UUID(as_uuid=True), primary_key=True,
                        server_default=text("gen_random_uuid()"), index=True)
    email      = Column(String(255), nullable=False, unique=True, index=True)
    is_active  = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False,
                        server_default=func.now())


# ── Schema ────────────────────────────────────────────────────────

class SubscribeRequest(BaseModel):
    email: EmailStr


# ── Email sender (runs in background so API responds instantly) ───

def send_welcome_email(to_email: str) -> None:
    """Fire-and-forget welcome email. Errors are logged, never raised."""
    if not resend.api_key:
        log.warning("[newsletter] RESEND_API_KEY not set — skipping welcome email")
        return
    try:
        resend.Emails.send(resend.SendEmailRequest({
            "from":    f"{FROM_NAME} <{FROM_EMAIL}>",
            "to":      [to_email],
            "subject": "Welcome to the Little Loot Family! 🎁",
            "html":    _welcome_html(to_email),
        }))
        log.info("[newsletter] Welcome email sent to %s", to_email)
    except Exception as exc:
        log.error("[newsletter] Failed to send welcome email to %s: %s", to_email, exc)


def _welcome_html(email: str) -> str:
    unsubscribe_url = f"{STORE_URL}/api/newsletter/unsubscribe?email={email}"
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Welcome to Little Loot!</title>
</head>
<body style="margin:0;padding:0;background:#f9f9f9;font-family:'Nunito',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9f9f9;padding:32px 0;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0"
               style="background:#ffffff;border-radius:20px;overflow:hidden;
                      box-shadow:0 4px 24px rgba(0,0,0,0.07);max-width:560px;width:100%;">

          <!-- Header -->
          <tr>
            <td style="background:#FEEAE4;padding:36px 40px;text-align:center;">
              <div style="font-size:48px;margin-bottom:8px;">🎁</div>
              <h1 style="margin:0;font-size:26px;font-weight:800;color:#1a1a1a;
                         font-family:'Nunito',Arial,sans-serif;">
                Welcome to the Little Loot Family!
              </h1>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:36px 40px;">
              <p style="margin:0 0 16px;font-size:16px;color:#444;line-height:1.6;">
                Hi there! 👋
              </p>
              <p style="margin:0 0 16px;font-size:16px;color:#444;line-height:1.6;">
                Thank you for joining the <strong>Little Loot</strong> family.
                You're now on the list for:
              </p>

              <table width="100%" cellpadding="0" cellspacing="0"
                     style="background:#FFF7ED;border-radius:14px;padding:20px 24px;
                            margin-bottom:28px;">
                <tr>
                  <td>
                    <p style="margin:0 0 10px;font-size:15px;color:#444;">
                      🎉 <strong>Exclusive offers</strong> — deals before anyone else
                    </p>
                    <p style="margin:0 0 10px;font-size:15px;color:#444;">
                      📦 <strong>New arrivals</strong> — first look at fresh products
                    </p>
                    <p style="margin:0;font-size:15px;color:#444;">
                      👶 <strong>Parenting tips</strong> — expert advice for your little ones
                    </p>
                  </td>
                </tr>
              </table>

              <!-- CTA -->
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td align="center">
                    <a href="{STORE_URL}"
                       style="display:inline-block;background:#F97316;color:#fff;
                              text-decoration:none;padding:14px 36px;border-radius:50px;
                              font-size:15px;font-weight:800;letter-spacing:0.3px;">
                      Shop Now →
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background:#f9f9f9;padding:24px 40px;text-align:center;
                       border-top:1px solid #eee;">
              <p style="margin:0 0 8px;font-size:13px;color:#999;">
                You received this because you subscribed at littleloot.in
              </p>
              <p style="margin:0;font-size:13px;color:#999;">
                <a href="{unsubscribe_url}"
                   style="color:#F97316;text-decoration:underline;">
                  Unsubscribe
                </a>
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


# ── Routes ────────────────────────────────────────────────────────

@newsletter_router.post("/subscribe", status_code=201)
async def subscribe(
    data: SubscribeRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_db),
):
    email = data.email.strip().lower()

    existing = (
        session.query(NewsletterSubscriber)
        .filter(NewsletterSubscriber.email == email)
        .first()
    )

    if existing:
        if not existing.is_active:
            existing.is_active = True
            session.commit()
            # Re-send welcome email for reactivated subscribers
            background_tasks.add_task(send_welcome_email, email)
            return {"message": "Subscribed successfully."}
        raise HTTPException(status_code=409, detail="Already subscribed.")

    session.add(NewsletterSubscriber(email=email))
    session.commit()

    # Send welcome email in background — doesn't block the API response
    background_tasks.add_task(send_welcome_email, email)

    return {"message": "Subscribed successfully."}


@newsletter_router.get("/unsubscribe")
async def unsubscribe(email: str, session: Session = Depends(get_db)):
    """
    GET so it works directly from an email link click.
    Returns a plain HTML confirmation page.
    """
    from fastapi.responses import HTMLResponse

    record = (
        session.query(NewsletterSubscriber)
        .filter(NewsletterSubscriber.email == email.strip().lower())
        .first()
    )
    if record:
        record.is_active = False
        session.commit()

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8"/>
      <meta name="viewport" content="width=device-width,initial-scale=1"/>
      <title>Unsubscribed — Little Loot</title>
      <style>
        body {{ margin:0; font-family: Arial, sans-serif; background: #f9f9f9;
                display: flex; align-items: center; justify-content: center;
                min-height: 100vh; }}
        .card {{ background:#fff; border-radius:20px; padding:48px 40px;
                 text-align:center; max-width:420px; box-shadow:0 4px 24px rgba(0,0,0,0.08); }}
        h1 {{ font-size:22px; color:#1a1a1a; margin:0 0 12px; }}
        p  {{ color:#666; font-size:15px; line-height:1.6; margin:0 0 24px; }}
        a  {{ display:inline-block; background:#F97316; color:#fff; padding:12px 28px;
              border-radius:50px; text-decoration:none; font-weight:700; font-size:14px; }}
      </style>
    </head>
    <body>
      <div class="card">
        <div style="font-size:48px;margin-bottom:16px;">👋</div>
        <h1>You've been unsubscribed</h1>
        <p>Sorry to see you go! You won't receive any more emails from Little Loot.</p>
        <a href="{STORE_URL}">Visit the store</a>
      </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)