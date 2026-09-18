# server/auth.py
"""External identity authentication and application JWT dependencies."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt as pyjwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from jwt import PyJWKClient
from sqlalchemy.orm import Session

from server.config import settings
from server.database import get_db
from server.models import User

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/provider", auto_error=False)

_microsoft_jwks = PyJWKClient(settings.microsoft_jwks_url)


def create_access_token(user_id: int, extra: Optional[dict] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    if extra:
        for key, value in extra.items():
            if key not in {"sub", "iat", "exp", "nbf", "iss", "aud"}:
                payload[key] = value
    return pyjwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> Optional[int]:
    try:
        payload = pyjwt.decode(
            token, settings.secret_key, algorithms=[settings.algorithm]
        )
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


def verify_google_id_token(raw_token: str) -> dict:
    if not settings.google_client_id:
        raise ValueError("Google authentication is not configured.")
    claims = google_id_token.verify_oauth2_token(
        raw_token,
        google_requests.Request(),
        settings.google_client_id,
    )
    if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise ValueError("Invalid Google token issuer.")
    if not claims.get("sub") or not claims.get("email"):
        raise ValueError("Google token does not contain a usable identity.")
    if claims.get("email_verified") is not True:
        raise ValueError("Google email is not verified.")
    return claims


def verify_microsoft_id_token(raw_token: str) -> dict:
    if not settings.microsoft_client_id:
        raise ValueError("Microsoft authentication is not configured.")

    try:
        unverified = pyjwt.decode(raw_token, options={"verify_signature": False})
        issuer = str(unverified.get("iss") or "")
        tenant_id = str(unverified.get("tid") or "")
        if not tenant_id or not issuer.startswith("https://login.microsoftonline.com/"):
            raise ValueError("Invalid Microsoft token issuer.")
        expected_issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        if issuer.rstrip("/") != expected_issuer:
            raise ValueError("Invalid Microsoft token issuer.")

        signing_key = _microsoft_jwks.get_signing_key_from_jwt(raw_token)
        claims = pyjwt.decode(
            raw_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.microsoft_client_id,
            issuer=expected_issuer,
            options={"require": ["exp", "iat", "sub", "tid", "aud", "iss"]},
        )
    except (pyjwt.PyJWTError, ValueError, TypeError) as exc:
        raise ValueError("Invalid Microsoft identity token.") from exc

    subject = claims.get("oid") or claims.get("sub")
    if not subject:
        raise ValueError("Microsoft token does not contain a stable subject.")

    email = claims.get("email") or claims.get("preferred_username")
    if not email or "@" not in str(email):
        raise ValueError("Microsoft account did not provide an email address.")

    return {
        **claims,
        "sub": str(subject),
        "email": str(email).strip().lower(),
    }


def verify_external_token(provider: str, raw_token: str) -> dict:
    provider = provider.strip().lower()
    if provider == "google":
        claims = verify_google_id_token(raw_token)
        return {
            "provider": "google",
            "subject": str(claims["sub"]),
            "email": str(claims["email"]).strip().lower(),
            "name": claims.get("name"),
        }
    if provider == "microsoft":
        claims = verify_microsoft_id_token(raw_token)
        return {
            "provider": "microsoft",
            "subject": str(claims["sub"]),
            "email": str(claims["email"]).strip().lower(),
            "name": claims.get("name"),
        }
    raise ValueError("Unsupported authentication provider.")


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
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
    "create_access_token",
    "decode_token",
    "verify_external_token",
    "verify_google_id_token",
    "verify_microsoft_id_token",
    "get_current_user",
    "get_optional_user",
    "oauth2_scheme",
]
