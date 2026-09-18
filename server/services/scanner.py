# server/services/scanner.py
"""
Nobitex market scanner.

The scanner is intentionally focused on one objective:
detect strong pumps and sustained upward trends on Nobitex markets.

It does NOT use:
- arbitrage
- price dislocation
- global-vs-local price comparison
- cross-exchange spread signals
- market-lag signals

Nobitex is the execution market and the primary market-data source.
Short-term history is kept in-process so repeated scans can measure
real price momentum rather than relying only on a 24h percentage.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import defaultdict, deque
from typing import Any, Optional

import pandas as pd

from server.config import settings
from server.database import SessionLocal
from trading.nobitex_client import NobitexClient

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
_CACHE: dict[str, Any] = {"rows": [], "updated_at": 0.0, "error": None, "source": None}

# Keep enough samples for roughly two hours at the default 5-minute scan interval.
_HISTORY: dict[str, deque[tuple[float, float, float]]] = defaultdict(lambda: deque(maxlen=30))


def get_cached() -> dict[str, Any]:
    with _LOCK:
        local_rows = list(_CACHE["rows"])
        local_updated = float(_CACHE["updated_at"])
        local_error = _CACHE["error"]
        local_source = _CACHE["source"]

    # The web service and dedicated worker are separate processes. Always
    # reconcile against the latest persisted snapshot so a worker refresh is
    # immediately visible to every web instance.
    rows = local_rows
    updated_at = local_updated
    error = local_error
    source = local_source
    try:
        from server.models import ScanSnapshot
        db = SessionLocal()
        try:
            snapshot = (
                db.query(ScanSnapshot)
                .order_by(ScanSnapshot.created_at.desc())
                .first()
            )
            if snapshot:
                persisted_updated = snapshot.created_at.timestamp() if snapshot.created_at else 0.0
                if persisted_updated > local_updated or not local_rows:
                    rows = json.loads(snapshot.payload)
                    updated_at = persisted_updated
                    error = None
                    source = "nobitex:persisted"
        finally:
            db.close()
    except Exception:
        logger.exception("Persisted scanner cache read failed")

    return {
        "rows": rows,
        "updated_at": updated_at,
        "error": error,
        "source": source,
        "count": len(rows),
    }

def is_fresh(max_age_seconds: int = 300) -> bool:
    with _LOCK:
        return bool(_CACHE["rows"]) and (time.time() - _CACHE["updated_at"]) < max_age_seconds


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except Exception:
            pass
    if isinstance(value, (int, str, bool)):
        return value
    return value


def _records_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    df = df.where(pd.notnull(df), None)
    return [{k: _json_safe(v) for k, v in row.items()} for row in df.to_dict(orient="records")]


def _build_nobitex_client() -> NobitexClient:
    # Scanner uses public Nobitex market endpoints; credentials are not required.
    return NobitexClient(quote_currency="IRT", testnet=False)


def _fetch_nobitex(limit: int) -> pd.DataFrame:
    client = _build_nobitex_client()
    rows = client.get_all_market_stats("IRT")
    if not rows:
        return pd.DataFrame()

    # Exclude unusably small/empty markets when possible, then cap the result.
    rows = [r for r in rows if float(r.get("Price") or 0) > 0]
    rows.sort(key=lambda r: float(r.get("Volume") or 0), reverse=True)
    return pd.DataFrame(rows[: max(1, int(limit))])


def _pct_change(old: float, new: float) -> float:
    if old <= 0 or new <= 0:
        return 0.0
    return (new - old) / old * 100.0


def _history_features(symbol: str, price: float, volume: float) -> dict[str, float]:
    now = time.time()
    with _LOCK:
        history = _HISTORY[symbol]
        history.append((now, price, volume))
        samples = list(history)

    def price_at(seconds: int) -> Optional[float]:
        target = now - seconds
        eligible = [x for x in samples if x[0] <= target]
        return eligible[-1][1] if eligible else None

    p5 = price_at(300)
    p15 = price_at(900)
    p30 = price_at(1800)

    return {
        "5m Change (%)": _pct_change(p5, price) if p5 else 0.0,
        "15m Change (%)": _pct_change(p15, price) if p15 else 0.0,
        "30m Change (%)": _pct_change(p30, price) if p30 else 0.0,
    }


def _classify_market(row: dict[str, Any]) -> tuple[str, float, float, list[str]]:
    """
    Return (condition, pump_score, trend_score, reasons).

    Scores are heuristic signal-strength measures, not profitability estimates.
    The first scan has no short-term history, so it cannot claim a fresh pump.
    """
    ch5 = float(row.get("5m Change (%)") or 0)
    ch15 = float(row.get("15m Change (%)") or 0)
    ch30 = float(row.get("30m Change (%)") or 0)
    ch24 = float(row.get("24h Change (%)") or 0)
    price = float(row.get("Price") or 0)
    high = float(row.get("Day High") or 0)
    low = float(row.get("Day Low") or 0)
    bid = float(row.get("Bid") or 0)
    ask = float(row.get("Ask") or 0)

    # Pump = acceleration over short windows + positive 24h confirmation.
    pump = (
        max(0.0, ch5) * 9.0
        + max(0.0, ch15) * 5.0
        + max(0.0, ch30) * 2.5
        + max(0.0, ch24) * 0.8
    )

    # Trend = persistent positive movement across multiple windows.
    trend = (
        max(0.0, ch15) * 5.0
        + max(0.0, ch30) * 4.0
        + max(0.0, ch24) * 1.5
    )
    if ch15 > 0 and ch30 > 0 and ch24 > 0:
        trend += 15

    if price > 0 and high > 0:
        high_distance = (high - price) / price * 100
        if high_distance <= 1.0:
            pump += 8
            trend += 8

    # Penalize a wide market so a nominal move is not treated as a clean entry.
    if bid > 0 and ask > 0 and ask >= bid:
        spread_pct = (ask - bid) / bid * 100
    else:
        spread_pct = 0.0
    if spread_pct > 2.0:
        pump -= 15
        trend -= 10

    pump = max(0.0, min(100.0, pump))
    trend = max(0.0, min(100.0, trend))

    reasons: list[str] = []
    if ch5 >= 1.0:
        reasons.append(f"5m momentum +{ch5:.2f}%")
    if ch15 >= 2.0:
        reasons.append(f"15m momentum +{ch15:.2f}%")
    if ch30 >= 3.0:
        reasons.append(f"30m momentum +{ch30:.2f}%")
    if ch24 >= 5.0:
        reasons.append(f"24h strength +{ch24:.2f}%")
    if price > 0 and high > 0 and (high - price) / price * 100 <= 1.0:
        reasons.append("near daily high")
    if spread_pct > 2.0:
        reasons.append("wide spread")

    if pump >= 70 and ch5 >= 1.0:
        condition = "STRONG_PUMP"
    elif pump >= 50 and ch15 >= 1.5:
        condition = "PUMP"
    elif trend >= 65 and ch15 > 0 and ch30 > 0:
        condition = "STRONG_UPTREND"
    elif trend >= 45 and ch15 > 0:
        condition = "UPTREND"
    elif ch24 > 0:
        condition = "WEAK_UPTREND"
    else:
        condition = "SIDEWAYS"

    return condition, round(pump, 2), round(trend, 2), reasons


def _enrich(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    enriched: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        symbol = str(row.get("Symbol") or "").upper()
        price = float(row.get("Price") or 0)
        volume = float(row.get("Volume") or 0)
        row.update(_history_features(symbol, price, volume))
        condition, pump, trend, reasons = _classify_market(row)
        row["Pump Score"] = pump
        row["Trend Score"] = trend
        row["Market Condition"] = condition
        row["Entry Ready"] = bool(
            (condition in {"STRONG_PUMP", "PUMP", "STRONG_UPTREND"} and pump >= 50)
            or (condition == "STRONG_UPTREND" and trend >= 65)
        )
        row["Signal"] = (
            "Strong Buy"
            if condition in {"STRONG_PUMP", "STRONG_UPTREND"}
            else "Buy Signal"
            if condition in {"PUMP", "UPTREND"}
            else "Neutral"
        )
        row["Score"] = round(max(pump, trend), 2)
        row["Risk"] = "High" if condition in {"STRONG_PUMP", "PUMP"} and float(row.get("5m Change (%)") or 0) >= 5 else "Medium"
        row["Risk_Level"] = row["Risk"]
        row["Reasons"] = "; ".join(reasons) if reasons else "No strong momentum confirmation"
        enriched.append(row)

    return pd.DataFrame(enriched)


def run_scan(persist_snapshot: bool = False) -> int:
    try:
        df = _fetch_nobitex(settings.scan_limit)
        if df is None or df.empty:
            with _LOCK:
                _CACHE["error"] = "no data returned by Nobitex"
                _CACHE["source"] = "nobitex"
            return 0

        df = _enrich(df)
        df = df.sort_values(
            by=["Entry Ready", "Score", "Pump Score", "Trend Score"],
            ascending=[False, False, False, False],
            na_position="last",
        ).reset_index(drop=True)

        records = _records_from_df(df)
        with _LOCK:
            _CACHE["rows"] = records
            _CACHE["updated_at"] = time.time()
            _CACHE["error"] = None
            _CACHE["source"] = "nobitex"

        logger.info("Nobitex scan complete | rows=%d | limit=%d", len(records), settings.scan_limit)

        if persist_snapshot:
            try:
                _persist_snapshot(records)
            except Exception as exc:
                logger.warning("Snapshot persist failed: %s", exc)
        return len(records)
    except Exception as exc:
        logger.exception("Nobitex scan failed: %s", exc)
        with _LOCK:
            _CACHE["error"] = "Nobitex scan failed"
            _CACHE["source"] = "nobitex"
        return 0


def _persist_snapshot(records: list[dict[str, Any]]) -> None:
    from server.models import ScanSnapshot
    db = SessionLocal()
    try:
        db.add(ScanSnapshot(row_count=len(records), payload=json.dumps(records, default=str)[:5_000_000]))
        db.commit()
    finally:
        db.close()


def get_symbol_price(symbol: str) -> Optional[float]:
    if not symbol:
        return None
    sym = symbol.upper().strip()
    with _LOCK:
        for row in _CACHE["rows"]:
            if str(row.get("Symbol") or "").upper() == sym:
                p = row.get("Price")
                if isinstance(p, (int, float)) and p > 0:
                    return float(p)
    return None


__all__ = ["run_scan", "get_cached", "is_fresh", "get_symbol_price"]
