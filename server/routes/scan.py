# server/routes/scan.py
"""
Scanner routes.

    GET  /api/scan           — cached result (public, always free)
    POST /api/scan/refresh   — run a fresh scan (rate-limited + quota)

Quota
-----
- Guests     : 3 refreshes / day per IP (soft, in-memory)
- Free users : FREE_DAILY_SCAN_LIMIT per UTC day (persisted)
- Pro users  : unlimited
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from server.auth import get_optional_user
from server.database import get_db
from server.models import User
from server.schemas import ScanRefreshResp, ScanResponse
from server.security import client_ip, client_ua, is_premium, limiter
from server.services import audit, plans, scanner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["scan"])


# ══════════════════════════════════════════════════════════
# Guest quota (in-memory, per IP, per UTC day)
# ══════════════════════════════════════════════════════════
_GUEST_LIMIT_PER_DAY = 3
_guest_lock = threading.Lock()
_guest_usage: dict[str, tuple[float, int]] = {}  # ip → (day_start_epoch, count)


def _utc_day_start() -> float:
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return day.timestamp()


def _guest_remaining(ip: str) -> int:
    day_start = _utc_day_start()
    with _guest_lock:
        entry = _guest_usage.get(ip)
        if entry is None or entry[0] != day_start:
            return _GUEST_LIMIT_PER_DAY
        return max(0, _GUEST_LIMIT_PER_DAY - entry[1])


def _guest_consume(ip: str) -> None:
    day_start = _utc_day_start()
    with _guest_lock:
        entry = _guest_usage.get(ip)
        if entry is None or entry[0] != day_start:
            _guest_usage[ip] = (day_start, 1)
        else:
            _guest_usage[ip] = (entry[0], entry[1] + 1)


# ══════════════════════════════════════════════════════════
# GET /api/scan  — cached
# ══════════════════════════════════════════════════════════
@router.get(
    "/scan",
    response_model=ScanResponse,
    summary="Get the most recent scanner result (cached)",
)
def get_scan() -> ScanResponse:
    data = scanner.get_cached()
    return ScanResponse(
        updated_at=data["updated_at"],
        count=data["count"],
        rows=data["rows"],
    )


# ══════════════════════════════════════════════════════════
# POST /api/scan/refresh
# ══════════════════════════════════════════════════════════
@router.post(
    "/scan/refresh",
    response_model=ScanRefreshResp,
    summary="Run a fresh scan (rate-limited)",
)
@limiter.limit("5/minute")
def refresh_scan(
    request: Request,
    response: Response,
    user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> ScanRefreshResp:
    ip = client_ip(request)
    ua = client_ua(request)

    # ── Guests: in-memory per-IP quota ──
    if user is None:
        remaining = _guest_remaining(ip)
        if remaining <= 0:
            audit.log_event(
                db, event=audit.EV_RATE_LIMITED, ip=ip, user_agent=ua,
                details={"scope": "guest_scan", "ip": ip},
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Daily guest scan limit reached. "
                    "Sign up for a free account to continue."
                ),
            )
        _guest_consume(ip)
        count = scanner.run_scan(persist_snapshot=True)
        return ScanRefreshResp(
            ok=True,
            count=count,
            remaining_today=max(0, remaining - 1),
        )

    # ── Registered users: plan-aware quota ──
    premium = is_premium(user)
    allowed, remaining = plans.can_scan(db, user, premium=premium)

    if not allowed:
        audit.log_event(
            db, user_id=user.id, event=audit.EV_RATE_LIMITED,
            ip=ip, user_agent=ua,
            details={"scope": "user_scan", "plan": user.plan},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Daily scan limit reached. "
                "Upgrade to Pro for unlimited scans."
            ),
        )

    if not premium:
        plans.record_scan(db, user.id)

    count = scanner.run_scan(persist_snapshot=True)
    new_remaining = -1 if premium else max(0, remaining - 1)

    logger.info(
        "Manual scan | user=%s plan=%s rows=%d remaining=%s",
        user.id, user.plan, count, new_remaining,
    )

    return ScanRefreshResp(
        ok=True,
        count=count,
        remaining_today=new_remaining,
    )


# ══════════════════════════════════════════════════════════
# GET /api/scan/status  — quota info for the current caller
# ══════════════════════════════════════════════════════════
@router.get(
    "/scan/status",
    summary="How many scans remain today (for the current caller)",
)
def scan_status(
    request: Request,
    user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> dict:
    if user is None:
        return {
            "authenticated": False,
            "remaining_today": _guest_remaining(client_ip(request)),
            "unlimited": False,
        }

    premium = is_premium(user)
    _, remaining = plans.can_scan(db, user, premium=premium)
    return {
        "authenticated": True,
        "plan": user.plan,
        "remaining_today": remaining,
        "unlimited": premium,
    }


__all__ = ["router"]