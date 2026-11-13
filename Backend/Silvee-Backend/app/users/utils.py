import os
import logging
from passlib.context import CryptContext
from datetime import datetime, timedelta
from typing import Union, Any, Optional, Tuple
from jose import jwt
from app.db import get_db, get_db_manually
from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session

from app.users.models import Users

logger = logging.getLogger(__name__)

# ── JWT config: read from environment, never hardcoded ───────────────────────
# SECURITY: JWT_SECRET_KEY must come from env (SECRET_KEY). If missing at
# startup, the server will fail fast rather than run with a known-weak secret.
from app.settings import settings

JWT_SECRET_KEY = settings.secret_key
ALGORITHM      = settings.algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes

COOKIE_ACCESS_KEY = 'user_session'

password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_user_by_id(db: Session, user_id: str) -> Optional[Users]:
    return db.query(Users).filter(Users.id == user_id).first()


def create_access_token(subject: Union[str, Any], session: Session, expires_delta: int = None) -> str:
    if expires_delta is not None:
        expires_delta = datetime.utcnow() + expires_delta
    else:
        expires_delta = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode = {"exp": expires_delta, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, ALGORITHM)
    return encoded_jwt


def _extract_token(request: Request) -> Optional[str]:
    # Authorization header first: it is what the website sends for the signed-in
    # user. The cookie is only a fallback — it can be stale (Google sign-in and
    # site logout never update it) or belong to a previous user on the browser.
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        if token:
            return token
    return request.cookies.get(COOKIE_ACCESS_KEY) or None


def decodeJWT(jwtoken: str, db: Session, return_object: bool = False) -> Tuple[Any, Any]:
    """
    Decode + verify a JWT.

    ALWAYS returns a 2-tuple (payload_or_user, error) so every caller can safely
    do `payload, err = decodeJWT(...)`.

    - return_object=False -> payload is a plain dict (shape used by JWTBearer)
    - return_object=True  -> payload is the Users ORM object
    """
    try:
        payload = jwt.decode(jwtoken, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        if not payload:
            return None, "Invalid token payload"

        user = get_user_by_id(db, payload.get('sub'))
        if not user:
            return None, "User not found"

        if return_object:
            return user, None

        return {
            'email':      user.email,
            'id':         user.id,
            'role':       user.role,
            'confirmed':  user.confirmed,
            'created_at': user.created_at,
            "name":       user.name,
            "phone":      user.phone,
            "address":    user.addresses,
        }, None
    except Exception:
        # SECURITY: do not log or return the raw exception — it can leak token
        # content, algorithm details, or internal stack traces to callers.
        logger.warning("JWT decode failed", exc_info=False)
        return None, "Invalid or expired token"


async def get_current_user(
    request: Request,
    db: Session,
    return_object: bool = False,
) -> Tuple[Any, Any]:
    try:
        token = _extract_token(request)
        if not token:
            return None, "Not authenticated"

        payload, error_message = decodeJWT(token, db, return_object)
        if not payload:
            return None, error_message or "Invalid or expired token"

        return payload, None
    except Exception:
        logger.warning("get_current_user failed", exc_info=False)
        return None, "Authentication error"


class JWTBearer(HTTPBearer):
    def __init__(self, auto_error: bool = True):
        super(JWTBearer, self).__init__(auto_error=auto_error)

    async def __call__(self, request: Request):
        # Try the Authorization header (the signed-in user on the website) first,
        # then the cookie. Previously the cookie won whenever present, so an old
        # expired/other-user cookie left in the browser made every request 401
        # (or act as the previous user) even with a valid Bearer token.
        candidates = []
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            bearer = auth_header.split(" ", 1)[1].strip()
            if bearer:
                candidates.append(bearer)
        cookie = request.cookies.get(COOKIE_ACCESS_KEY)
        if cookie and cookie not in candidates:
            candidates.append(cookie)

        if candidates:
            db = get_db_manually()
            try:
                for access_key in candidates:
                    isTokenValid, user = self.verify_jwt(access_key, db)
                    if isTokenValid:
                        return user
                    if access_key is candidates[0] and auth_header:
                        # An invalid/expired Bearer token must not silently fall back
                        # to a cookie for a different session.
                        break
            finally:
                db.close()
            if self.auto_error:
                raise HTTPException(status_code=401, detail="Invalid or expired token.")
            return None
        else:
            if self.auto_error:
                raise HTTPException(status_code=401, detail="Authentication required.")
            return None

    def verify_jwt(self, jwtoken: str, db) -> Tuple[bool, Any]:
        isTokenValid: bool = False
        try:
            payload, _ = decodeJWT(jwtoken, db)
        except Exception:
            payload = None
        if payload:
            return True, payload
        return isTokenValid, None
