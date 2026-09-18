"""Load persisted market snapshots and run the cost-aware backtest."""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from sqlalchemy.orm import Session
from server.config import settings
from server.models import ScanSnapshot
from analytics.backtest import BacktestConfig, run_backtest

def load_rows(db: Session, days: int) -> list[dict[str, Any]]:
    days=max(1,min(int(days or settings.backtest_default_days),settings.backtest_max_days))
    cutoff=datetime.now(timezone.utc)-timedelta(days=days)
    snapshots=db.query(ScanSnapshot).filter(ScanSnapshot.created_at>=cutoff).order_by(ScanSnapshot.created_at.asc()).all()
    rows=[]
    for snap in snapshots:
        try:
            payload=json.loads(snap.payload)
        except (TypeError,ValueError):
            continue
        if not isinstance(payload,list):continue
        ts=snap.created_at.isoformat() if snap.created_at else None
        for row in payload:
            if isinstance(row,dict):
                item=dict(row)
                item.setdefault("timestamp",ts)
                rows.append(item)
    return rows

def run_persisted_backtest(db: Session, days: int, **overrides: Any):
    rows=load_rows(db,days)
    cfg=BacktestConfig(
        initial_capital=float(overrides.get("initial_capital",10_000_000)),
        fee_pct_per_side=float(overrides.get("fee_pct_per_side",0.10)),
        slippage_pct_per_side=float(overrides.get("slippage_pct_per_side",0.10)),
        entry_delay_scans=int(overrides.get("entry_delay_scans",0)),
        stop_loss_pct=float(overrides.get("stop_loss_pct",3.0)),
        trailing_activation_pct=float(overrides.get("trailing_activation_pct",3.0)),
        trailing_distance_pct=float(overrides.get("trailing_distance_pct",3.0)),
        take_profit_pct=float(overrides.get("take_profit_pct",50.0)),
        capital_usage_pct=float(overrides.get("capital_usage_pct",90.0)),
        max_open_positions=int(overrides.get("max_open_positions",1)),
        signal_min_global_move_pct=float(overrides.get("signal_min_global_move_pct",1.2)),
        signal_min_observed_move_pct=float(overrides.get("signal_min_observed_move_pct",0.7)),
        max_chase_pct=float(overrides.get("max_chase_pct",0.7)),
        max_spread_pct=float(overrides.get("max_spread_pct",1.2)),
    )
    report=run_backtest(rows,cfg)
    data=report.to_dict()
    data["days"]=max(1,min(int(days or settings.backtest_default_days),settings.backtest_max_days))
    data["snapshots"]=db.query(ScanSnapshot).filter(ScanSnapshot.created_at>=datetime.now(timezone.utc)-timedelta(days=data["days"])).count()
    data["snapshot_rows"]=len(rows)
    data["data_quality"]={"has_global_data":sum(1 for r in rows if r.get("GlobalPriceUSD"))>0,"rows":len(rows)}
    return data
