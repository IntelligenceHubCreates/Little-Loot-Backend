# app/notifications/routers.py
"""
Customer notification endpoints (Phase 14, Stage 2a).
Auth via JWTBearer (matches the rest of the customer API). Returns the user's
real Notification rows for the account bell.
"""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.users.utils import JWTBearer
from app.notifications.models import Notification

notifications_router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


def _serialize(n: Notification) -> dict:
    return {
        "id":         str(n.id),
        "type":       n.type,
        "title":      n.title,
        "body":       n.body,
        "link":       n.link,
        "meta":       n.meta or {},
        "is_read":    bool(n.is_read),
        "created_at": n.created_at.isoformat() if n.created_at else "",
    }


@notifications_router.get("/my")
def my_notifications(limit: int = 30, unread_only: bool = False,
                     session: Session = Depends(get_db), user=Depends(JWTBearer())):
    q = session.query(Notification).filter(Notification.user_id == user["id"])
    if unread_only:
        q = q.filter(Notification.is_read == False)  # noqa: E712
    rows = q.order_by(Notification.created_at.desc()).limit(min(limit, 100)).all()
    unread = (session.query(Notification)
              .filter(Notification.user_id == user["id"], Notification.is_read == False)  # noqa: E712
              .count())
    return {"data": [_serialize(n) for n in rows], "unread_count": unread}


@notifications_router.post("/{notification_id}/read")
def mark_read(notification_id: str, session: Session = Depends(get_db), user=Depends(JWTBearer())):
    n = session.query(Notification).filter(
        Notification.id == notification_id, Notification.user_id == user["id"]
    ).first()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    if not n.is_read:
        from datetime import datetime, timezone
        n.is_read = True
        n.read_at = datetime.now(timezone.utc)
        session.commit()
    return {"status": "ok"}


@notifications_router.post("/read-all")
def mark_all_read(session: Session = Depends(get_db), user=Depends(JWTBearer())):
    from datetime import datetime, timezone
    rows = session.query(Notification).filter(
        Notification.user_id == user["id"], Notification.is_read == False  # noqa: E712
    ).all()
    now = datetime.now(timezone.utc)
    for n in rows:
        n.is_read = True
        n.read_at = now
    session.commit()
    return {"status": "ok", "marked": len(rows)}