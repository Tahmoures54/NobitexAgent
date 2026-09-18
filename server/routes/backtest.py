"""Backtest API over persisted Global + Nobitex snapshots."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from server.auth import get_current_user
from server.config import settings
from server.database import get_db
from server.models import ScanSnapshot, User
from server.services.backtesting import run_persisted_backtest

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.get("", summary="Run profitability and performance backtest")
def backtest(
    days: int = Query(7, ge=1, le=30),
    initial_capital: float = Query(10_000_000, gt=0),
    fee_pct_per_side: float = Query(0.10, ge=0, le=5),
    slippage_pct_per_side: float = Query(0.10, ge=0, le=5),
    entry_delay_scans: int = Query(0, ge=0, le=10),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return run_persisted_backtest(
            db,
            days,
            initial_capital=initial_capital,
            fee_pct_per_side=fee_pct_per_side,
            slippage_pct_per_side=slippage_pct_per_side,
            entry_delay_scans=entry_delay_scans,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Backtest failed.") from exc


@router.get("/status", summary="Show market-history collection status")
def backtest_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.market_history_retention_days)
    snapshots = (
        db.query(ScanSnapshot)
        .filter(ScanSnapshot.created_at >= cutoff)
        .order_by(ScanSnapshot.created_at.asc())
        .all()
    )
    now = datetime.now(timezone.utc)
    first = snapshots[0].created_at if snapshots else None
    last = snapshots[-1].created_at if snapshots else None

    def age_seconds(value):
        if not value:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return max(0, int((now - value).total_seconds()))

    total_rows = sum(int(s.row_count or 0) for s in snapshots)
    global_rows = 0
    for snap in snapshots:
        try:
            payload = json.loads(snap.payload)
            if isinstance(payload, list):
                global_rows += sum(
                    1 for row in payload
                    if isinstance(row, dict) and row.get("GlobalPriceUSD") not in (None, "", 0)
                )
        except (TypeError, ValueError):
            continue

    last_age = age_seconds(last)
    if not snapshots:
        status = "WAITING"
    elif last_age is not None and last_age <= max(900, settings.scan_interval_minutes * 60 * 3):
        status = "COLLECTING"
    else:
        status = "STALE"

    return {
        "status": status,
        "snapshots": len(snapshots),
        "rows": total_rows,
        "global_rows": global_rows,
        "global_coverage_pct": round((global_rows / total_rows) * 100, 1) if total_rows else 0.0,
        "first_snapshot": first.isoformat() if first else None,
        "last_snapshot": last.isoformat() if last else None,
        "last_age_seconds": last_age,
        "scan_interval_minutes": settings.scan_interval_minutes,
        "retention_days": settings.market_history_retention_days,
        "max_rows_per_scan": settings.market_history_max_rows_per_scan,
        "message": "Snapshots are collected automatically by the dedicated worker.",
    }
