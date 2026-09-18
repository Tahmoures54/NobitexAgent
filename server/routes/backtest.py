"""Backtest API over persisted Global + Nobitex snapshots."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from server.auth import get_current_user
from server.database import get_db
from server.models import User
from server.services.backtesting import run_persisted_backtest

router=APIRouter(prefix="/api/backtest",tags=["backtest"])

@router.get("",summary="Run profitability backtest over stored market snapshots")
def backtest(
    days:int=Query(7,ge=1,le=30),
    initial_capital:float=Query(10_000_000,gt=0),
    fee_pct_per_side:float=Query(0.10,ge=0,le=5),
    slippage_pct_per_side:float=Query(0.10,ge=0,le=5),
    entry_delay_scans:int=Query(0,ge=0,le=10),
    user:User=Depends(get_current_user),
    db:Session=Depends(get_db),
):
    try:
        return run_persisted_backtest(
            db,days,
            initial_capital=initial_capital,
            fee_pct_per_side=fee_pct_per_side,
            slippage_pct_per_side=slippage_pct_per_side,
            entry_delay_scans=entry_delay_scans,
        )
    except Exception as exc:
        raise HTTPException(status_code=500,detail="Backtest failed.") from exc

@router.get("/status",summary="Show whether enough market history exists")
def backtest_status(user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    from server.models import ScanSnapshot
    count=db.query(ScanSnapshot).count()
    return {"snapshots":count,"retention_days":30,"message":"Snapshots are collected automatically by the scanner."}
