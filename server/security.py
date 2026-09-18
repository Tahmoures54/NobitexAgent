# server/security.py
"""
Rate limiting + plan-based authorization dependencies.

Rate limits are attached per-route using the `@limiter.limit(...)`
decorator. See `routes/auth.py` and `routes/scan.py` for usage.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address

from server.auth import get_current_user
from server.config import settings
from server.models import User

# ── Rate limiter ───────────────────────────────────────────
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=settings.redis_url or "memory://",
)


# ── Plan helpers ───────────────────────────────────────────
PREMIUM_PLANS = frozenset({"pro", "lifetime"})


def is_premium(user: Optional[User]) -> bool:
    """True iff the user is on an active paid plan."""
    if user is None:
        return False
    if user.plan not in PREMIUM_PLANS:
        return False
    if user.plan_expires_at is None:
        return True
    expires = user.plan_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > datetime.now(timezone.utc)


# ── FastAPI dependencies ───────────────────────────────────
def require_premium(user: User = Depends(get_current_user)) -> User:
    """Reject with 402 unless the user has an active paid plan."""
    if not is_premium(user):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="This feature requires a Premium plan.",
        )
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Reject unless the user's email matches ADMIN_EMAIL."""
    admin_email = (settings.admin_email or "").strip().lower()
    if not admin_email or user.email.lower() != admin_email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return user


# ── Client info helpers (for audit log) ────────────────────
def client_ip(request: Request) -> str:
    """Best-effort client IP behind a reverse proxy."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else "unknown"


def client_ua(request: Request) -> str:
    return (request.headers.get("user-agent") or "")[:255]


__all__ = [
    "limiter",
    "is_premium",
    "require_premium",
    "require_admin",
    "client_ip",
    "client_ua",
    "PREMIUM_PLANS",
]