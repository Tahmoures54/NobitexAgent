# server/routes/admin.py
"""
Admin-only routes. Guarded by `require_admin`.

    GET /admin/stats              — high-level KPIs
    GET /admin/users              — paginated user list
    GET /admin/audit              — recent audit events
    POST /admin/users/{id}/plan   — change a user's plan
    POST /admin/scan/run          — force a scan immediately
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from server.database import get_db
from server.models import Trade, User
from server.schemas import AdminStatsOut, UserOut
from server.security import client_ip, client_ua, require_admin
from server.services import audit, scanner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ══════════════════════════════════════════════════════════
# Schemas
# ══════════════════════════════════════════════════════════
class PlanChangeReq(BaseModel):
    plan: str = Field(pattern="^(free|pro|lifetime)$")
    expires_at: Optional[datetime] = None
    note: Optional[str] = Field(default=None, max_length=255)


class UserPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[UserOut]


# ══════════════════════════════════════════════════════════
# Stats
# ══════════════════════════════════════════════════════════
@router.get("/stats", response_model=AdminStatsOut, summary="High-level KPIs")
def stats(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminStatsOut:
    total_users = db.query(func.count(User.id)).scalar() or 0
    premium_users = (
        db.query(func.count(User.id))
        .filter(User.plan.in_(("pro", "lifetime")))
        .scalar()
        or 0
    )
    total_trades = db.query(func.count(Trade.id)).scalar() or 0
    open_trades = (
        db.query(func.count(Trade.id)).filter(Trade.status == "open").scalar() or 0
    )
    return AdminStatsOut(
        total_users=int(total_users),
        premium_users=int(premium_users),
        free_users=int(total_users - premium_users),
        total_trades=int(total_trades),
        open_trades=int(open_trades),
    )


# ══════════════════════════════════════════════════════════
# Users
# ══════════════════════════════════════════════════════════
@router.get("/users", response_model=UserPage, summary="Paginated user list")
def users(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    plan: Optional[str] = Query(None, pattern="^(free|pro|lifetime)$"),
) -> UserPage:
    q = db.query(User)
    if plan:
        q = q.filter(User.plan == plan)

    total = q.count()
    rows = q.order_by(User.id.desc()).offset(offset).limit(limit).all()

    return UserPage(
        total=int(total),
        offset=offset,
        limit=limit,
        items=[UserOut.model_validate(u) for u in rows],
    )


# ══════════════════════════════════════════════════════════
# Audit
# ══════════════════════════════════════════════════════════
@router.get("/audit", summary="Recent audit events")
def audit_events(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    user_id: Optional[int] = Query(None),
    event: Optional[str] = Query(None, max_length=64),
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict]:
    rows = audit.recent_events(
        db, user_id=user_id, event=event, limit=limit
    )
    return [
        {
            "id": r.id,
            "event": r.event,
            "user_id": r.user_id,
            "ip": r.ip,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "details": r.details,
        }
        for r in rows
    ]


# ══════════════════════════════════════════════════════════
# Change plan
# ══════════════════════════════════════════════════════════
@router.post(
    "/users/{user_id}/plan",
    response_model=UserOut,
    summary="Change a user's plan",
)
def set_plan(
    request: Request,
    user_id: int,
    req: PlanChangeReq,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserOut:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    prev_plan = target.plan
    target.plan = req.plan
    target.plan_expires_at = req.expires_at if req.plan != "free" else None

    # Normalize tz: SQLite stores naive datetimes but we want UTC aware
    if target.plan_expires_at is not None and target.plan_expires_at.tzinfo is None:
        target.plan_expires_at = target.plan_expires_at.replace(tzinfo=timezone.utc)

    db.commit()
    db.refresh(target)

    audit.log_event(
        db, user_id=admin.id, event=audit.EV_PLAN_CHANGED,
        ip=client_ip(request), user_agent=client_ua(request),
        details={
            "target_user": target.id,
            "from": prev_plan,
            "to": target.plan,
            "expires_at": (
                target.plan_expires_at.isoformat()
                if target.plan_expires_at
                else None
            ),
            "note": req.note,
        },
    )

    logger.info(
        "Plan changed | admin=%d target=%d %s → %s",
        admin.id, target.id, prev_plan, target.plan,
    )

    return UserOut.model_validate(target)


# ══════════════════════════════════════════════════════════
# Force scan
# ══════════════════════════════════════════════════════════
@router.post("/scan/run", summary="Force a scan right now")
def force_scan(
    _admin: User = Depends(require_admin),
) -> dict:
    count = scanner.run_scan()
    return {"status": "ok", "rows": count}


__all__ = ["router"]