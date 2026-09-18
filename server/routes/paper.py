# server/routes/paper.py
"""
Paper trading routes. All require authentication.

    POST /api/paper/open            — open a new trade
    POST /api/paper/{id}/close      — close an open trade manually
    GET  /api/paper/list            — list the user's trades
    GET  /api/paper/stats           — aggregate statistics
    DELETE /api/paper/{id}          — cancel/delete (only if still open)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from server.auth import get_current_user
from server.database import get_db
from server.models import Trade, User
from server.schemas import (
    CloseTradeReq,
    OpenTradeReq,
    PaperStatsOut,
    TradeOut,
)
from server.security import client_ip, client_ua
from server.services import audit, paper as paper_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/paper", tags=["paper"])


# ══════════════════════════════════════════════════════════
# Open
# ══════════════════════════════════════════════════════════
@router.post(
    "/open",
    response_model=TradeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Open a new paper trade",
)
def open_trade(
    request: Request,
    req: OpenTradeReq,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TradeOut:
    # Guard: prevent absurd position sizes for tiny notional
    if req.size_usd < 1.0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="size_usd must be at least 1.0",
        )

    trade = paper_svc.open_trade(db, user.id, req)

    logger.info(
        "Paper open via API | user=%d trade=%d %s",
        user.id, trade.id, trade.symbol,
    )
    return TradeOut.model_validate(trade)


# ══════════════════════════════════════════════════════════
# Close
# ══════════════════════════════════════════════════════════
@router.post(
    "/{trade_id}/close",
    response_model=TradeOut,
    summary="Close an open paper trade",
)
def close_trade(
    request: Request,
    trade_id: int,
    req: CloseTradeReq,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TradeOut:
    trade = paper_svc.close_trade(
        db, user.id, trade_id, req.price, req.reason
    )
    if trade is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Open trade not found.",
        )
    logger.info(
        "Paper close via API | user=%d trade=%d pnl=%.4f%%",
        user.id, trade.id, trade.pnl_pct or 0.0,
    )
    return TradeOut.model_validate(trade)


# ══════════════════════════════════════════════════════════
# List
# ══════════════════════════════════════════════════════════
@router.get(
    "/list",
    response_model=list[TradeOut],
    summary="List the user's paper trades",
)
def list_trades(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(200, ge=1, le=1000),
) -> list[TradeOut]:
    rows = paper_svc.list_trades(db, user.id, limit=limit)
    return [TradeOut.model_validate(t) for t in rows]


# ══════════════════════════════════════════════════════════
# Stats
# ══════════════════════════════════════════════════════════
@router.get(
    "/stats",
    response_model=PaperStatsOut,
    summary="Aggregate statistics for paper trades",
)
def get_stats(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PaperStatsOut:
    return PaperStatsOut(**paper_svc.stats(db, user.id))


# ══════════════════════════════════════════════════════════
# Delete an open trade (cancel)
# ══════════════════════════════════════════════════════════
@router.delete(
    "/{trade_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete an open paper trade (no PnL recorded)",
)
def delete_trade(
    request: Request,
    trade_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    trade = (
        db.query(Trade)
        .filter(
            Trade.id == trade_id,
            Trade.user_id == user.id,
            Trade.status == "open",
        )
        .first()
    )
    if trade is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Open trade not found.",
        )

    db.delete(trade)
    db.commit()

    audit.log_event(
        db, user_id=user.id, event="paper_delete",
        ip=client_ip(request), user_agent=client_ua(request),
        details={"trade_id": trade_id, "symbol": trade.symbol},
    )

    logger.info("Paper delete via API | user=%d trade=%d", user.id, trade_id)
    return {"status": "ok", "deleted": trade_id}


__all__ = ["router"]