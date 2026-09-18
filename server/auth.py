# server/auth.py
"""Passwordless TOTP authentication for Google Authenticator and Microsoft Authenticator."""
from __future__ import annotations

import base64
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Optional

import jwt as pyjwt
import pyotp
import qrcode
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from server.config import settings
from server.database import get_db
from server.models import User

logger = logging.getLogger(__name__)
oauth2_scheme = HTTPBearer(auto_error=False)


def _fernet() -> Fernet:
    """Build the Fernet cipher from the dedicated TOTP encryption key."""
    key = base64.urlsafe_b64encode(
        hashlib.sha256(settings.totp_encryption_key.encode("utf-8")).digest()
    )
    return Fernet(key)


def encrypt_totp_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_totp_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ValueError("Stored authenticator secret cannot be decrypted.") from exc


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def build_totp_setup(secret: str, email: str) -> tuple[str, str]:
    uri = pyotp.TOTP(secret).provisioning_uri(
        name=email,
        issuer_name=settings.app_name,
    )
    qr = qrcode.QRCode(box_size=8, border=4)
    qr.add_data(uri)
    qr.make(fit=True)
    image = qr.make_image()
    buf = BytesIO()
    image.save(buf, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    return uri, data_uri


def verify_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(str(code), valid_window=1)


def create_access_token(user_id: int, extra: Optional[dict] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": str(user_id), "iat": int(now.timestamp()), "exp": int(expire.timestamp())}
    if extra:
        for key, value in extra.items():
            if key not in {"sub", "iat", "exp", "nbf", "iss", "aud"}:
                payload[key] = value
    return pyjwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> Optional[int]:
    try:
        payload = pyjwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        sub = payload.get("sub")
        return int(sub) if sub is not None else None
    except (pyjwt.PyJWTError, ValueError, TypeError, OverflowError):
        return None


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    uid = decode_token(credentials.credentials)
    if uid is None:
        raise _unauthorized("Invalid or expired token")
    user = db.get(User, uid)
    if user is None:
        raise _unauthorized("User not found")
    if not user.is_active or not user.totp_enabled:
        raise _unauthorized("Authenticator verification is required")
    return user


def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Optional[User]:
    if not credentials or credentials.scheme.lower() != "bearer":
        return None
    uid = decode_token(credentials.credentials)
    if uid is None:
        return None
    user = db.get(User, uid)
    if user is None or not user.is_active or not user.totp_enabled:
        return None
    return user


__all__ = [
    "create_access_token", "decode_token", "encrypt_totp_secret",
    "decrypt_totp_secret", "generate_totp_secret", "build_totp_setup",
    "verify_totp", "get_current_user", "get_optional_user", "oauth2_scheme",
]
