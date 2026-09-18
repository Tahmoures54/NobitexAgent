# server/auth.py
"""
Password hashing, JWT issuance, and auth dependencies.

Uses:
    - passlib[bcrypt]  → password hashing
    - python-jose      → JWT (HS256)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from server.config import settings
from server.database import get_db
from server.models import User

logger = logging.getLogger(__name__)

# ── Password hashing ───────────────────────────────────────
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


def hash_password(password: str) -> str:
    """Hash a plaintext password (bcrypt, cost 12)."""
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time password check."""
    try:
        return _pwd_context.verify(plain, hashed)
    except Exception as exc:
        logger.warning("Password verification error: %s", exc)
        return False


# ── JWT ────────────────────────────────────────────────────
# auto_error=False so we can provide our own 401 message and
# also support optional auth (e.g. `/scan` accessible to guests).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)


def create_access_token(user_id: int, extra: Optional[dict] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    if extra:
        # Never allow optional claims to overwrite security-critical claims.
        for key, value in extra.items():
            if key not in {"sub", "iat", "exp", "nbf", "iss", "aud"}:
                payload[key] = value
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> Optional[int]:
    """Return user_id if token is valid, else None."""
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.algorithm]
        )
        sub = payload.get("sub")
        if sub is None:
            return None
        return int(sub)
    except (JWTError, ValueError, TypeError, OverflowError):
        return None


# ── FastAPI dependencies ───────────────────────────────────
def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Require a valid token. Raises 401 otherwise."""
    if not token:
        raise _unauthorized()
    uid = decode_token(token)
    if uid is None:
        raise _unauthorized("Invalid or expired token")
    user = db.get(User, uid)
    if user is None:
        raise _unauthorized("User not found")
    if not user.is_active:
        raise _unauthorized("Account is disabled")
    return user


def get_optional_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Return the user if the token is valid, otherwise None. Never raises."""
    if not token:
        return None
    uid = decode_token(token)
    if uid is None:
        return None
    user = db.get(User, uid)
    if user is None or not user.is_active:
        return None
    return user


__all__ = [
    "hash_password",
    "verify_password",
    "create_access_token",
    "decode_token",
    "get_current_user",
    "get_optional_user",
    "oauth2_scheme",
]