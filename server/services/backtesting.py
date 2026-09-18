"""Load persisted market snapshots and run the performance backtest."""
from __future__ import annotations

import json
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from analytics.backtest import BacktestConfig, run_backtest
from server.config import settings
from server.models import ScanSnapshot


def _to_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def load_rows(db: Session, days: int) -> list[dict[str, Any]]:
    days = max(1, min(int(days or settings.backtest_default_days), settings.backtest_max_days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    snapshots = (
        db.query(ScanSnapshot)
        .filter(ScanSnapshot.created_at >= cutoff)
        .order_by(ScanSnapshot.created_at.asc())
        .all()
    )

    rows: list[dict[str, Any]] = []
    for snap in snapshots:
        try:
            payload = json.loads(snap.payload)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, list):
            continue
        ts = snap.created_at.isoformat() if snap.created_at else None
        for row in payload:
            if isinstance(row, dict):
                item = dict(row)
                item.setdefault("timestamp", ts)
                rows.append(item)

    # Reconstruct the observed global movement from persisted global prices.
    # This keeps the backtest useful after the worker has restarted and its
    # in-memory scanner history has been cleared.
    history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=5))
    rows.sort(key=lambda r: str(r.get("timestamp") or r.get("Timestamp") or ""))
    for row in rows:
        symbol = str(row.get("symbol") or row.get("Symbol") or "").strip().upper()
        global_price = row.get("GlobalPriceUSD")
        try:
            global_price = float(global_price)
        except (TypeError, ValueError):
            global_price = 0.0

        if symbol and global_price > 0:
            prior = history[symbol][-1] if history[symbol] else None
            if prior and prior > 0:
                row["ObservedGlobalPct"] = (global_price / prior - 1.0) * 100.0
            else:
                row["ObservedGlobalPct"] = 0.0
            history[symbol].append(global_price)
        else:
            row.setdefault("ObservedGlobalPct", 0.0)

    return rows


def _config(overrides: dict[str, Any], delay: int) -> BacktestConfig:
    return BacktestConfig(
        initial_capital=float(overrides.get("initial_capital", 10_000_000)),
        fee_pct_per_side=float(overrides.get("fee_pct_per_side", 0.10)),
        slippage_pct_per_side=float(overrides.get("slippage_pct_per_side", 0.10)),
        entry_delay_scans=int(delay),
        stop_loss_pct=float(overrides.get("stop_loss_pct", 3.0)),
        trailing_activation_pct=float(overrides.get("trailing_activation_pct", 3.0)),
        trailing_distance_pct=float(overrides.get("trailing_distance_pct", 3.0)),
        take_profit_pct=float(overrides.get("take_profit_pct", 50.0)),
        capital_usage_pct=float(overrides.get("capital_usage_pct", 90.0)),
        max_open_positions=int(overrides.get("max_open_positions", 1)),
        signal_min_global_move_pct=float(overrides.get("signal_min_global_move_pct", 1.2)),
        signal_min_observed_move_pct=float(overrides.get("signal_min_observed_move_pct", 0.7)),
        max_chase_pct=float(overrides.get("max_chase_pct", 0.7)),
        max_spread_pct=float(overrides.get("max_spread_pct", 1.2)),
    )


def _quality(rows: list[dict[str, Any]], days: int) -> dict[str, Any]:
    timestamps = [_to_dt(r.get("timestamp") or r.get("Timestamp")) for r in rows]
    timestamps = [x for x in timestamps if x]
    global_rows = sum(1 for r in rows if r.get("GlobalPriceUSD") not in (None, "", 0))
    first = min(timestamps) if timestamps else None
    last = max(timestamps) if timestamps else None
    duration_hours = ((last - first).total_seconds() / 3600.0) if first and last else 0.0
    expected_scans = max(1, int(days * 24 * 60 / max(1, settings.scan_interval_minutes)))
    actual_snapshots = len({str(r.get("timestamp")) for r in rows if r.get("timestamp")})
    coverage = min(100.0, actual_snapshots / expected_scans * 100.0)
    return {
        "has_global_data": global_rows > 0,
        "global_coverage_pct": round(global_rows / len(rows) * 100.0, 1) if rows else 0.0,
        "first_timestamp": first.isoformat() if first else None,
        "last_timestamp": last.isoformat() if last else None,
        "duration_hours": round(duration_hours, 2),
        "expected_scan_count": expected_scans,
        "actual_scan_count": actual_snapshots,
        "scan_coverage_pct": round(coverage, 1),
        "sufficient_for_analysis": bool(
            first and last and duration_hours >= 24.0 and global_rows > 0
        ),
        "note": (
            "At least 24 hours of persisted data with global prices is required "
            "before treating performance metrics as meaningful."
        ),
    }


def run_persisted_backtest(db: Session, days: int, **overrides: Any) -> dict[str, Any]:
    days = max(1, min(int(days or settings.backtest_default_days), settings.backtest_max_days))
    rows = load_rows(db, days)
    quality = _quality(rows, days)

    primary_delay = int(overrides.get("entry_delay_scans", 0))
    primary = run_backtest(rows, _config(overrides, primary_delay)).to_dict()

    delay_comparison = []
    for delay in (0, 1, 2):
        report = run_backtest(rows, _config(overrides, delay)).to_dict()
        delay_comparison.append({
            "delay_scans": delay,
            "return_pct": report["return_pct"],
            "net_profit": report["net_profit"],
            "trades": report["trades"],
            "win_rate_pct": report["win_rate_pct"],
            "profit_factor": report["profit_factor"],
            "expectancy_pct": report["expectancy_pct"],
            "max_drawdown_pct": report["max_drawdown_pct"],
            "total_fees": report["total_fees"],
            "total_slippage_cost": report["total_slippage_cost"],
        })

    primary["days"] = days
    primary["entry_delay_scans"] = primary_delay
    primary["snapshots"] = db.query(ScanSnapshot).filter(
        ScanSnapshot.created_at >= datetime.now(timezone.utc) - timedelta(days=days)
    ).count()
    primary["snapshot_rows"] = len(rows)
    primary["data_quality"] = quality
    primary["delay_comparison"] = delay_comparison
    return primary
