"""
Transactional email service for Little Loot.
All public functions are fire-and-forget: they log on failure but NEVER raise,
so a broken email path cannot crash an order or auth flow.
"""
from __future__ import annotations

import logging
import threading
from typing import List

import resend
from html import escape as _html_escape
from app.settings import settings
from app.products.customization import describe as describe_customization

logger = logging.getLogger(__name__)

# ── Brand palette (inline styles — email clients require this) ────────────────
_BG      = "#FFFDF9"
_PRIMARY = "#3B0F4E"
_ACCENT  = "#F4623A"
_CARD    = "#FFFFFF"
_BORDER  = "#EFE7EC"
_MUTED   = "#8A7891"
_BODY    = "#5B4266"


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _base_template(title: str, preview: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background:{_BG};font-family:system-ui,-apple-system,Arial,sans-serif">
  <div style="display:none;max-height:0;overflow:hidden;color:{_BG};font-size:1px">{preview}</div>
  <table width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};padding:32px 16px">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%">

        <!-- Brand header -->
        <tr>
          <td align="center" style="padding:0 0 24px">
            <div style="display:inline-block;background:{_PRIMARY};padding:14px 28px;border-radius:14px">
              <span style="font-size:22px;font-weight:900;color:#FFFFFF;letter-spacing:-0.3px">&#127873; Little Loot</span>
            </div>
          </td>
        </tr>

        <!-- Card -->
        <tr>
          <td style="background:{_CARD};border-radius:20px;padding:36px 32px;border:1.5px solid {_BORDER}">
            {body_html}
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td align="center" style="padding:28px 0 0;color:{_MUTED};font-size:12px;line-height:2">
            Questions? Reply to this email or reach us at
            <a href="mailto:{settings.support_email}" style="color:{_ACCENT};text-decoration:none">{settings.support_email}</a><br>
            &#169; Little Loot &mdash; Gifts that spark joy &bull;
            <a href="{settings.frontend_url}" style="color:{_MUTED};text-decoration:none">littlelootgifts.com</a>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _heading(icon: str, text: str) -> str:
    return (
        f'<div style="text-align:center;margin-bottom:28px">'
        f'<div style="font-size:44px;margin-bottom:10px;line-height:1">{icon}</div>'
        f'<h2 style="margin:0;font-size:24px;font-weight:900;color:{_PRIMARY};line-height:1.2">{text}</h2>'
        f'</div>'
    )


def _order_pill(order_short: str) -> str:
    return (
        f'<div style="text-align:center;margin-bottom:20px">'
        f'<span style="display:inline-block;background:#F3E8FF;color:{_PRIMARY};'
        f'font-size:12px;font-weight:800;padding:5px 14px;border-radius:20px;letter-spacing:0.8px">'
        f'ORDER #{order_short}'
        f'</span></div>'
    )


def _cta_button(href: str, label: str) -> str:
    return (
        f'<a href="{href}" style="display:block;text-align:center;'
        f'background:linear-gradient(135deg,{_ACCENT} 0%,{_PRIMARY} 100%);'
        f'color:#FFFFFF;font-weight:800;font-size:15px;padding:16px 24px;'
        f'border-radius:12px;text-decoration:none;margin-top:28px;letter-spacing:0.2px">'
        f'{label}</a>'
    )


def _items_table(items: list) -> str:
    if not items:
        return ""
    rows = "".join(
        f"<tr>"
        f"<td style='padding:11px 0;color:{_BODY};font-size:14px;line-height:1.4;"
        f"border-bottom:1px solid {_BORDER}'>"
        f"{it.get('name') or 'Product'}"
        + (f"<span style='color:{_ACCENT};font-size:12px;font-weight:700;display:block'>&#9998; {_html_escape(describe_customization(it.get('customization')))}</span>"
           if it.get('customization') else "")
        + f"<span style='color:{_MUTED};font-size:12px;display:block'>Qty: {it.get('quantity', 1)}</span>"
        f"</td>"
        f"<td style='padding:11px 0;color:{_PRIMARY};font-weight:700;font-size:14px;"
        f"text-align:right;vertical-align:top;border-bottom:1px solid {_BORDER}'>"
        f"&#8377;{float(it.get('price', 0)) * int(it.get('quantity', 1)):.0f}"
        f"</td></tr>"
        for it in items
    )
    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' "
        f"style='margin:20px 0;border-top:1.5px solid {_BORDER}'>{rows}</table>"
    )


def _totals_block(
    subtotal: float = 0.0,
    discount: float = 0.0,
    delivery: float = 0.0,
    total: float = 0.0,
) -> str:
    rows = ""
    if subtotal:
        rows += (
            f"<tr><td style='padding:5px 0;font-size:13px;color:{_BODY}'>Subtotal</td>"
            f"<td style='padding:5px 0;font-size:13px;color:{_BODY};text-align:right'>&#8377;{subtotal:.0f}</td></tr>"
        )
    if discount:
        rows += (
            f"<tr><td style='padding:5px 0;font-size:13px;color:#16a34a'>Coupon discount</td>"
            f"<td style='padding:5px 0;font-size:13px;color:#16a34a;text-align:right'>&#8722;&#8377;{discount:.0f}</td></tr>"
        )
    delivery_label = "Free delivery" if delivery == 0 else "Delivery"
    delivery_val   = "Free" if delivery == 0 else f"&#8377;{delivery:.0f}"
    rows += (
        f"<tr><td style='padding:5px 0;font-size:13px;color:{_BODY}'>{delivery_label}</td>"
        f"<td style='padding:5px 0;font-size:13px;color:{_BODY};text-align:right'>{delivery_val}</td></tr>"
    )
    rows += (
        f"<tr style='border-top:1.5px solid {_BORDER}'>"
        f"<td style='padding:12px 0 0;font-size:16px;font-weight:800;color:{_PRIMARY}'>Total Paid</td>"
        f"<td style='padding:12px 0 0;font-size:16px;font-weight:800;color:{_ACCENT};text-align:right'>&#8377;{total:.0f}</td>"
        f"</tr>"
    )
    return f"<table width='100%' cellpadding='0' cellspacing='0'>{rows}</table>"


def _address_block(address: str) -> str:
    if not address:
        return ""
    return (
        f'<div style="background:#F9F5FF;border-radius:12px;padding:14px 18px;margin:20px 0 0;'
        f'font-size:13px;color:{_BODY};line-height:1.8">'
        f'<strong style="color:{_PRIMARY};display:block;margin-bottom:4px">&#128205; Delivering to</strong>'
        f'{address}'
        f'</div>'
    )


# ── Core dispatch (never raises) ─────────────────────────────────────────────

def _dispatch(subject: str, to: str, html: str, reply_to: str | None = None) -> None:
    """Queue an email on a background thread so a slow/unreachable Resend never
    delays the HTTP response (signup and password reset used to hang ~60s, which
    also leaked whether an email was registered via response timing)."""
    if not settings.resend_api_key:
        logger.debug("Email skipped — RESEND_API_KEY not set: %s → %s", subject, to)
        return
    threading.Thread(target=_send, args=(subject, to, html, reply_to), daemon=True).start()


def _send(subject: str, to: str, html: str, reply_to: str | None = None) -> None:
    try:
        resend.api_key = settings.resend_api_key
        resend.Emails.send({
            "from":     f"Little Loot <{settings.resend_from_email}>",
            "reply_to": reply_to or settings.support_email,
            "to":       [to],
            "subject": subject,
            "html":    html,
        })
        logger.info("Email sent '%s' → %s", subject, to)
    except Exception:
        logger.warning("Email failed '%s' → %s", subject, to, exc_info=True)


# ── Public send functions ─────────────────────────────────────────────────────

def send_order_confirmed(
    *,
    user_email: str,
    user_name: str,
    order_id: str,
    items: List[dict],
    subtotal: float = 0.0,
    discount: float = 0.0,
    delivery: float = 0.0,
    total: float,
    shipping_address: str = "",
) -> None:
    """Send order-confirmation email after successful payment."""
    order_short = str(order_id)[:8].upper()
    body = (
        _heading("&#127881;", "Your order is confirmed!")
        + _order_pill(order_short)
        + f'<p style="color:{_BODY};font-size:14px;line-height:1.8;margin:0 0 4px">'
        f'Hi <strong>{user_name or "there"}</strong>,</p>'
        f'<p style="color:{_BODY};font-size:14px;line-height:1.8;margin:0 0 20px">'
        f'Thank you for shopping with <strong>Little Loot</strong>! '
        f'We are carefully packing your order and will ship it soon. &#127873;</p>'
        + _items_table(items)
        + _totals_block(subtotal=subtotal, discount=discount, delivery=delivery, total=total)
        + _address_block(shipping_address)
        + _cta_button(f"{settings.frontend_url}/orders", "Track My Order &#8594;")
    )
    _dispatch(
        subject=f"Order confirmed! #{order_short} \U0001f381 — Little Loot",
        to=user_email,
        html=_base_template(
            "Order Confirmed — Little Loot",
            f"Your order #{order_short} is confirmed and being packed with love!",
            body,
        ),
    )


def send_order_shipped(
    *,
    user_email: str,
    user_name: str,
    order_id: str,
    items: List[dict],
    total: float,
) -> None:
    """Send shipping notification when admin marks order as shipped."""
    order_short = str(order_id)[:8].upper()
    body = (
        _heading("&#128666;", "Your order is on its way!")
        + _order_pill(order_short)
        + f'<p style="color:{_BODY};font-size:14px;line-height:1.8;margin:0 0 4px">'
        f'Hi <strong>{user_name or "there"}</strong>,</p>'
        f'<p style="color:{_BODY};font-size:14px;line-height:1.8;margin:0 0 20px">'
        f'Great news! Your Little Loot order has been shipped and is on its way to you. '
        f'Hang tight &#128230;</p>'
        + _items_table(items)
        + f"<table width='100%' cellpadding='0' cellspacing='0' style='margin-top:4px'>"
        f"<tr><td style='padding:12px 0 0;font-size:15px;font-weight:800;color:{_PRIMARY}'>Order Total</td>"
        f"<td style='padding:12px 0 0;font-size:15px;font-weight:800;color:{_ACCENT};text-align:right'>&#8377;{total:.0f}</td>"
        f"</tr></table>"
        + _cta_button(f"{settings.frontend_url}/orders", "Track My Order &#8594;")
    )
    _dispatch(
        subject=f"Your order #{order_short} has shipped! \U0001f69a — Little Loot",
        to=user_email,
        html=_base_template(
            "Order Shipped — Little Loot",
            f"Order #{order_short} is on its way to you!",
            body,
        ),
    )


def send_order_delivered(
    *,
    user_email: str,
    user_name: str,
    order_id: str,
    items: List[dict],
    total: float,
) -> None:
    """Send delivery confirmation and nudge for a review."""
    order_short = str(order_id)[:8].upper()
    body = (
        _heading("&#127881;", "Order delivered!")
        + _order_pill(order_short)
        + f'<p style="color:{_BODY};font-size:14px;line-height:1.8;margin:0 0 4px">'
        f'Hi <strong>{user_name or "there"}</strong>,</p>'
        f'<p style="color:{_BODY};font-size:14px;line-height:1.8;margin:0 0 20px">'
        f'Your Little Loot order has been delivered! We hope you absolutely love your gifts. &#129392;</p>'
        + _items_table(items)
        + f"<table width='100%' cellpadding='0' cellspacing='0' style='margin-top:4px'>"
        f"<tr><td style='padding:12px 0 0;font-size:15px;font-weight:800;color:{_PRIMARY}'>Order Total</td>"
        f"<td style='padding:12px 0 0;font-size:15px;font-weight:800;color:{_ACCENT};text-align:right'>&#8377;{total:.0f}</td>"
        f"</tr></table>"
        + f'<div style="background:#F0FDF4;border-radius:12px;padding:16px 18px;margin:24px 0 0;'
        f'font-size:13px;color:#166534;line-height:1.8;text-align:center">'
        f'&#11088; Enjoying your order? '
        f'<a href="{settings.frontend_url}/orders" style="color:{_ACCENT};font-weight:700;text-decoration:none">'
        f'Leave a review</a> and help others discover great gifts!</div>'
        + _cta_button(f"{settings.frontend_url}/orders", "Write a Review &#8594;")
    )
    _dispatch(
        subject=f"Your order #{order_short} has been delivered! \U0001f389 — Little Loot",
        to=user_email,
        html=_base_template(
            "Order Delivered — Little Loot",
            f"Order #{order_short} delivered. We hope you love it!",
            body,
        ),
    )


def send_order_cancelled(
    *,
    user_email: str,
    user_name: str,
    order_id: str,
    total: float,
    cancelled_by: str = "you",
) -> None:
    """Send cancellation notice (works for both user-initiated and admin-initiated cancellations)."""
    order_short = str(order_id)[:8].upper()
    if cancelled_by == "admin":
        intro = (
            f"We regret to inform you that your order <strong>#{order_short}</strong> "
            f"has been cancelled by our team."
        )
    else:
        intro = (
            f"As requested, your order <strong>#{order_short}</strong> has been successfully cancelled."
        )
    body = (
        _heading("&#10060;", "Order cancelled")
        + _order_pill(order_short)
        + f'<p style="color:{_BODY};font-size:14px;line-height:1.8;margin:0 0 4px">'
        f'Hi <strong>{user_name or "there"}</strong>,</p>'
        f'<p style="color:{_BODY};font-size:14px;line-height:1.8;margin:0 0 20px">'
        f'{intro}</p>'
        + f"<table width='100%' cellpadding='0' cellspacing='0'>"
        f"<tr><td style='padding:12px 0;font-size:15px;font-weight:800;color:{_PRIMARY}'>Order Total</td>"
        f"<td style='padding:12px 0;font-size:15px;font-weight:800;color:{_BODY};text-align:right'>&#8377;{total:.0f}</td>"
        f"</tr></table>"
        + f'<div style="background:#FFF7ED;border-radius:12px;padding:14px 18px;margin:20px 0 0;'
        f'font-size:13px;color:#92400E;line-height:1.8">'
        f'&#128181; If you paid online, any amount charged will be refunded to your original payment method '
        f'within <strong>5&ndash;7 business days</strong>. If you have questions, '
        f'please reply to this email or contact our support team.</div>'
        + _cta_button(f"{settings.frontend_url}/products", "Continue Shopping &#8594;")
    )
    _dispatch(
        subject=f"Order #{order_short} cancelled — Little Loot",
        to=user_email,
        html=_base_template(
            "Order Cancelled — Little Loot",
            f"Your order #{order_short} has been cancelled.",
            body,
        ),
    )


def send_welcome_email(*, user_email: str, user_name: str) -> None:
    """Send a warm welcome email when a new user registers."""
    first_name = (user_name or "").split()[0] if user_name else "there"
    body = (
        _heading("&#127873;", f"Welcome to Little Loot, {first_name}!")
        + f'<p style="color:{_BODY};font-size:14px;line-height:1.8;margin:0 0 24px">'
        f'We are so happy to have you! Little Loot is your destination for amazing gifts &mdash; '
        f'from stationery and toys to beauty and bags. Explore our curated collections '
        f'and find something truly special today.</p>'
        + f'<div style="background:#F9F5FF;border-radius:14px;padding:20px 22px;margin:0 0 8px">'
        f'<p style="margin:0 0 12px;font-size:13px;font-weight:800;color:{_PRIMARY}">What you get with Little Loot:</p>'
        f'<table width="100%" cellpadding="0" cellspacing="0">'
        f'<tr><td style="padding:6px 0;font-size:13px;color:{_BODY}">&#10003;&nbsp;&nbsp;Free shipping on orders above &#8377;499</td></tr>'
        f'<tr><td style="padding:6px 0;font-size:13px;color:{_BODY}">&#10003;&nbsp;&nbsp;Free replacement if an item arrives damaged</td></tr>'
        f'<tr><td style="padding:6px 0;font-size:13px;color:{_BODY}">&#10003;&nbsp;&nbsp;Wishlist, order tracking &amp; gift messages</td></tr>'
        f'<tr><td style="padding:6px 0;font-size:13px;color:{_BODY}">&#10003;&nbsp;&nbsp;Exclusive deals on new arrivals</td></tr>'
        f'</table></div>'
        + _cta_button(f"{settings.frontend_url}/products", "Start Exploring &#8594;")
    )
    _dispatch(
        subject="Welcome to Little Loot! \U0001f381",
        to=user_email,
        html=_base_template(
            "Welcome — Little Loot",
            "Your gifting journey starts here — explore amazing gifts today!",
            body,
        ),
    )


def send_return_pickup_scheduled(
    *,
    user_email: str,
    user_name: str,
    order_id: str,
    courier_name: str = "",
    awb_number: str = "",
    tracking_url: str = "",
    pickup_date: str = "",
) -> None:
    """Tell the customer a pickup is booked for the item they reported as damaged/wrong."""
    import html as _html
    order_short = str(order_id)[:8].upper()
    rows = []
    if courier_name:
        rows.append(("Courier", courier_name))
    if awb_number:
        rows.append(("AWB / Tracking no.", awb_number))
    if pickup_date:
        rows.append(("Expected pickup", pickup_date))
    details = ""
    if rows:
        details = (
            f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#F9F5FF;border-radius:14px;padding:14px 18px;margin:0 0 8px">'
            + "".join(
                f'<tr><td style="padding:6px 0;font-size:13px;color:{_MUTED}">{_html.escape(k)}</td>'
                f'<td style="padding:6px 0;font-size:13px;font-weight:800;color:{_PRIMARY};text-align:right">{_html.escape(v)}</td></tr>'
                for k, v in rows
            )
            + "</table>"
        )
    body = (
        _heading("&#128666;", "Pickup booked for your item")
        + _order_pill(order_short)
        + f'<p style="color:{_BODY};font-size:14px;line-height:1.8;margin:0 0 4px">'
        f'Hi <strong>{_html.escape(user_name or "there")}</strong>,</p>'
        f'<p style="color:{_BODY};font-size:14px;line-height:1.8;margin:0 0 16px">'
        f'We&rsquo;ve arranged a courier to collect the item you reported. Please keep it packed '
        f'(original packaging if you still have it) and ready to hand over. Once it reaches us, '
        f'we&rsquo;ll send your replacement.</p>'
        + details
        + _cta_button(tracking_url or f"{settings.frontend_url}/account", "Track Pickup &#8594;" if tracking_url else "View My Report &#8594;")
    )
    _dispatch(
        subject=f"Pickup booked for order #{order_short} \U0001f69a — Little Loot",
        to=user_email,
        html=_base_template(
            "Pickup Booked — Little Loot",
            f"A courier will collect the item you reported on order #{order_short}.",
            body,
        ),
    )


# ── Bulk order enquiries ─────────────────────────────────────────────────────

def _bulk_rows(rows: list) -> str:
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#F9F5FF;border-radius:14px;padding:14px 18px;margin:0 0 8px">'
        + "".join(
            f'<tr><td style="padding:6px 0;font-size:13px;color:{_MUTED};vertical-align:top;width:40%">{_html_escape(k)}</td>'
            f'<td style="padding:6px 0;font-size:13px;font-weight:700;color:{_PRIMARY}">{_html_escape(str(v))}</td></tr>'
            for k, v in rows if v not in (None, "")
        )
        + "</table>"
    )


def send_bulk_enquiry_to_store(e: dict) -> None:
    """New bulk enquiry → store inbox. Reply-to is the customer when they gave an email."""
    phone = e.get("phone") or ""
    wa = f"https://wa.me/91{phone}" if phone else ""
    body = (
        _heading("&#128230;", "New bulk order enquiry")
        + f'<p style="color:{_BODY};font-size:14px;line-height:1.8;margin:0 0 16px;text-align:center">'
        f'<strong>{_html_escape(e.get("reference", ""))}</strong> · reply within 24 hours</p>'
        + _bulk_rows([
            ("Product", (e.get("product_name") or "—") + (f" ({e['product_sku']})" if e.get("product_sku") else "")),
            ("Quantity", f"{e.get('quantity')} units"),
            ("Needed by", e.get("needed_by")),
            ("Personalisation", {"yes": "Yes", "no": "No"}.get(e.get("personalise") or "", "")),
            ("Name", e.get("name")),
            ("Phone", phone),
            ("Email", e.get("email")),
            ("City", e.get("city")),
            ("Notes", e.get("notes")),
        ])
        + (_cta_button(wa, "Reply on WhatsApp &#8594;") if wa else "")
    )
    _dispatch(
        subject=f"Bulk enquiry {e.get('reference', '')}: {e.get('quantity')} × {e.get('product_name') or 'products'}",
        to=settings.support_email,
        html=_base_template("New bulk enquiry — Little Loot", f"{e.get('quantity')} units requested by {e.get('name')}", body),
        reply_to=e.get("email") or None,
    )


def send_bulk_enquiry_confirmation(e: dict) -> None:
    """Confirmation to the customer that their bulk enquiry was received."""
    if not e.get("email"):
        return
    first = (e.get("name") or "there").split()[0]
    body = (
        _heading("&#128230;", "We&rsquo;ve got your bulk order request")
        + f'<p style="color:{_BODY};font-size:14px;line-height:1.8;margin:0 0 4px">Hi <strong>{_html_escape(first)}</strong>,</p>'
        f'<p style="color:{_BODY};font-size:14px;line-height:1.8;margin:0 0 16px">'
        f'Thanks for thinking of Little Loot for your bulk order! Our team will call or WhatsApp you within '
        f'<strong>24 hours</strong> with the best price and delivery timeline.</p>'
        + _bulk_rows([
            ("Reference", e.get("reference")),
            ("Product", e.get("product_name") or "As described"),
            ("Quantity", f"{e.get('quantity')} units"),
            ("Needed by", e.get("needed_by")),
        ])
        + _cta_button(f"{settings.frontend_url}/bulk-orders", "Make another request &#8594;")
    )
    _dispatch(
        subject=f"Your bulk order request {e.get('reference', '')} — Little Loot",
        to=e["email"],
        html=_base_template("Bulk order request received — Little Loot", "We'll reply within 24 hours with a quote.", body),
    )

