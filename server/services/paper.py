# server/services/paper.py
"""
Paper trading service.

Responsibilities
----------------
- Open a simulated position with a stop loss and take profit.
- Close a position manually or automatically (SL/TP).
- Scan all open trades of a user and auto-close those touched by price.
- Compute summary statistics.

Position sizing
---------------
Fixed-notional per trade: `size_usd` is the USD amount committed.
`position_size` stored in DB is the coin quantity.

PnL
---
For a long trade:
    pnl_pct = (exit - entry) / entry * 100
    pnl_usd = (exit - entry) * position_size
For a short trade the signs are inverted.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from server.models import Trade
from server.config import settings
from server.schemas import OpenTradeReq
from server.services import audit

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════
# Open
# ══════════════════════════════════════════════════════════
def open_trade(db: Session, user_id: int, req: OpenTradeReq) -> Trade:
    """Create a new open paper trade."""
    symbol = req.symbol.upper().strip()
    side = req.side.lower()
    entry = float(req.price)

    closed_pnl = sum((t.pnl_usd or 0.0) for t in db.query(Trade).filter(
        Trade.user_id == user_id, Trade.status == "closed"
    ).all())
    open_notional = sum((t.notional or 0.0) for t in db.query(Trade).filter(
        Trade.user_id == user_id, Trade.status == "open"
    ).all())
    available_cash = max(0.0, float(settings.paper_initial_cash) + closed_pnl - open_notional)
    strategy_notional = available_cash * float(settings.paper_capital_usage_pct) / 100.0
    notional = strategy_notional
    if notional <= 0:
        raise ValueError("Insufficient virtual paper cash for a new position.")
    position_size = notional / entry

    if side == "long":
        sl = entry * (1.0 - req.stop_loss_pct / 100.0)
        tp = entry * (1.0 + req.take_profit_pct / 100.0)
    else:  # short
        sl = entry * (1.0 + req.stop_loss_pct / 100.0)
        tp = entry * (1.0 - req.take_profit_pct / 100.0)

    trade = Trade(
        user_id=user_id,
        symbol=symbol,
        side=side,
        entry_price=entry,
        entry_time=_utcnow(),
        entry_signal=req.signal,
        position_size=position_size,
        notional=notional,
        stop_loss=sl,
        take_profit=tp,
        highest_price=entry if side == "long" else None,
        lowest_price=entry if side == "short" else None,
        trailing_stop=None,
        trailing_active=False,
        status="open",
    )
    db.add(trade)
    db.commit()
    db.refresh(trade)

    logger.info(
        "Paper OPEN | user=%d %s %s | entry=%.6f size=%.6f notional=%.2f "
        "SL=%.6f TP=%.6f",
        user_id, side.upper(), symbol, entry, position_size, notional, sl, tp,
    )

    try:
        audit.log_event(
            db, user_id=user_id, event="paper_open",
            details={"symbol": symbol, "side": side, "price": entry,
                     "size_usd": notional},
        )
    except Exception:
        pass

    return trade


# ══════════════════════════════════════════════════════════
# Close
# ══════════════════════════════════════════════════════════
def close_trade(
    db: Session,
    user_id: int,
    trade_id: int,
    price: float,
    reason: str,
) -> Optional[Trade]:
    """Close an open trade. Returns the updated Trade or None."""
    trade: Optional[Trade] = (
        db.query(Trade)
        .filter(
            Trade.id == trade_id,
            Trade.user_id == user_id,
            Trade.status == "open",
        )
        .first()
    )
    if trade is None:
        return None

    price = float(price)
    if price <= 0:
        logger.warning("close_trade called with non-positive price: %s", price)
        return None

    sign = -1.0 if trade.side == "short" else 1.0
    pnl_pct = sign * (price - trade.entry_price) / trade.entry_price * 100.0
    pnl_usd = sign * (price - trade.entry_price) * trade.position_size

    trade.exit_price = price
    trade.exit_time = _utcnow()
    trade.exit_reason = (reason or "Manual")[:64]
    trade.pnl_pct = round(pnl_pct, 6)
    trade.pnl_usd = round(pnl_usd, 6)
    trade.status = "closed"

    db.commit()
    db.refresh(trade)

    logger.info(
        "Paper CLOSE | user=%d %s | entry=%.6f exit=%.6f | pnl=%.4f%% $%.2f | %s",
        user_id, trade.symbol, trade.entry_price, price,
        pnl_pct, pnl_usd, trade.exit_reason,
    )

    try:
        audit.log_event(
            db, user_id=user_id, event="paper_close",
            details={
                "symbol": trade.symbol, "trade_id": trade.id,
                "exit_price": price, "reason": trade.exit_reason,
                "pnl_pct": trade.pnl_pct, "pnl_usd": trade.pnl_usd,
            },
        )
    except Exception:
        pass

    return trade


# ══════════════════════════════════════════════════════════
# Auto-close (SL/TP watcher)
# ══════════════════════════════════════════════════════════
def check_exits(
    db: Session,
    user_id: int,
    price_map: dict[str, float],
) -> int:
    """
    Auto-close all open trades of `user_id` whose SL or TP has been hit.

    Uses the SL/TP levels stored on the trade (not the current price),
    so the recorded exit price is the level that triggered the close.
    """
    if not price_map:
        return 0

    open_trades = (
        db.query(Trade)
        .filter(Trade.user_id == user_id, Trade.status == "open")
        .all()
    )
    if not open_trades:
        return 0

    closed = 0
    for t in open_trades:
        sym = (t.symbol or "").upper()
        current = price_map.get(sym)
        if not current or current <= 0:
            continue

        trigger_price: Optional[float] = None
        trigger_reason: Optional[str] = None

        activation = float(settings.trailing_activation_pct)
        distance = float(settings.trailing_distance_pct)

        if t.side == "long":
            t.highest_price = max(float(t.highest_price or t.entry_price), current)
            gain_pct = (t.highest_price - t.entry_price) / t.entry_price * 100.0
            if gain_pct >= activation:
                t.trailing_active = True
                candidate = t.highest_price * (1.0 - distance / 100.0)
                t.trailing_stop = max(float(t.trailing_stop or 0.0), candidate)
                t.stop_loss = max(float(t.stop_loss or 0.0), t.trailing_stop)
            if t.stop_loss is not None and current <= t.stop_loss:
                trigger_price = t.stop_loss
                trigger_reason = "Trailing Stop" if t.trailing_active else "Stop Loss"
            elif t.take_profit is not None and current >= t.take_profit:
                trigger_price = t.take_profit
                trigger_reason = "Take Profit"
        else:
            t.lowest_price = min(float(t.lowest_price or t.entry_price), current)
            gain_pct = (t.entry_price - t.lowest_price) / t.entry_price * 100.0
            if gain_pct >= activation:
                t.trailing_active = True
                candidate = t.lowest_price * (1.0 + distance / 100.0)
                t.trailing_stop = min(float(t.trailing_stop or float("inf")), candidate)
                t.stop_loss = min(float(t.stop_loss or float("inf")), t.trailing_stop)
            if t.stop_loss is not None and current >= t.stop_loss:
                trigger_price = t.stop_loss
                trigger_reason = "Trailing Stop" if t.trailing_active else "Stop Loss"
            elif t.take_profit is not None and current <= t.take_profit:
                trigger_price = t.take_profit
                trigger_reason = "Take Profit"

        db.flush()

        if trigger_price is None or trigger_reason is None:
            continue

        try:
            result = close_trade(db, user_id, t.id, trigger_price, trigger_reason)
            if result is not None:
                closed += 1
        except Exception as exc:
            logger.exception(
                "Auto-close failed | user=%d trade=%d: %s", user_id, t.id, exc
            )

    return closed


# ══════════════════════════════════════════════════════════
# Stats
# ══════════════════════════════════════════════════════════
def stats(db: Session, user_id: int) -> dict:
    """Aggregate statistics for the user's paper trades."""
    all_trades = db.query(Trade).filter(Trade.user_id == user_id).all()
    total = len(all_trades)
    closed = [t for t in all_trades if t.status == "closed"]
    open_trades = total - len(closed)

    wins = [t for t in closed if (t.pnl_pct or 0) > 0]
    losses = [t for t in closed if (t.pnl_pct or 0) <= 0]

    total_pnl_usd = sum((t.pnl_usd or 0.0) for t in closed)
    avg_pnl_pct = (
        sum((t.pnl_pct or 0.0) for t in closed) / len(closed) if closed else 0.0
    )
    win_rate = (len(wins) / len(closed) * 100.0) if closed else 0.0

    return {
        "total_trades": total,
        "open_trades": open_trades,
        "closed_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 2),
        "total_pnl_usd": round(total_pnl_usd, 2),
        "avg_pnl_pct": round(avg_pnl_pct, 4),
    }


def list_trades(db: Session, user_id: int, limit: int = 200) -> list[Trade]:
    return (
        db.query(Trade)
        .filter(Trade.user_id == user_id)
        .order_by(Trade.entry_time.desc())
        .limit(int(limit))
        .all()
    )


__all__ = ["open_trade", "close_trade", "check_exits", "stats", "list_trades"]