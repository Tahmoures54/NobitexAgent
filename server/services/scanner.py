# server/services/scanner.py
"""
Scanner service — fetches market data, applies strategy, caches result.

Design
------
- In-process cache protected by a lock. Safe for the single-worker
  MVP (`uvicorn --workers 1`). Multi-worker deployment must switch to
  Redis or a DB-backed cache.
- Data source priority: CoinMarketCap (if key) → CoinGecko (free).
- The strategy engine (`analysis.signals.apply_strategy_to_df`) is
  called unchanged.
- Payload is JSON-safe: NaN/Inf → None.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from typing import Any, Optional

import pandas as pd

from server.config import settings
from server.database import SessionLocal

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# In-memory cache
# ══════════════════════════════════════════════════════════
_LOCK = threading.RLock()
_CACHE: dict[str, Any] = {
    "rows": [],
    "updated_at": 0.0,
    "error": None,
    "source": None,
}


def get_cached() -> dict[str, Any]:
    """Return a shallow copy of the current cache."""
    with _LOCK:
        return {
            "rows": list(_CACHE["rows"]),
            "updated_at": float(_CACHE["updated_at"]),
            "error": _CACHE["error"],
            "source": _CACHE["source"],
            "count": len(_CACHE["rows"]),
        }


def is_fresh(max_age_seconds: int = 300) -> bool:
    with _LOCK:
        if not _CACHE["rows"]:
            return False
        return (time.time() - _CACHE["updated_at"]) < max_age_seconds


# ══════════════════════════════════════════════════════════
# JSON sanitization
# ══════════════════════════════════════════════════════════
def _json_safe(value: Any) -> Any:
    """Replace NaN/Inf with None; leave other values untouched."""
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (int, str, bool)):
        return value
    return value


def _records_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert a DataFrame to a list of JSON-safe dicts."""
    if df is None or df.empty:
        return []
    df = df.where(pd.notnull(df), None)
    records = df.to_dict(orient="records")
    return [{k: _json_safe(v) for k, v in row.items()} for row in records]


# ══════════════════════════════════════════════════════════
# Data fetchers
# ══════════════════════════════════════════════════════════
def _fetch_coingecko(limit: int) -> pd.DataFrame:
    """Free fallback. No API key needed."""
    try:
        from api.api_coingecko import CoinGeckoClient

        client = CoinGeckoClient()
        data = client.get_listings(limit=min(int(limit), 250))
        if not data or not isinstance(data, list):
            return pd.DataFrame()

        rows: list[dict[str, Any]] = []
        for c in data:
            rows.append({
                "Rank": c.get("market_cap_rank"),
                "Name": c.get("name"),
                "Symbol": (c.get("symbol") or "").upper(),
                "Slug": c.get("id"),
                "AssetKey": f"cg:{c.get('id', '')}",
                "Price": c.get("current_price"),
                "1h Change (%)": c.get("price_change_percentage_1h_in_currency"),
                "24h Change (%)": c.get("price_change_percentage_24h"),
                "7d Change (%)": c.get("price_change_percentage_7d_in_currency"),
                "Volume": c.get("total_volume"),
                "Market Cap": c.get("market_cap"),
            })
        df = pd.DataFrame(rows)
        if not df.empty and "Market Cap" in df.columns and "Volume" in df.columns:
            with pd.option_context("mode.use_inf_as_na", True):
                df["Turnover Ratio (%)"] = (
                    df["Volume"] / df["Market Cap"] * 100
                ).where(df["Market Cap"] > 0)
        return df
    except Exception as exc:
        logger.warning("CoinGecko fetch failed: %s", exc)
        return pd.DataFrame()


def _fetch_market_data(limit: int) -> tuple[pd.DataFrame, str]:
    """
    Try CMC first (if key is configured), fall back to CoinGecko.

    Returns
    -------
    (df, source_name)
    """
    # 1. Try CMC / Binance provider if key present
    if settings.cryptosscanner_cmc_key:
        try:
            import os
            os.environ.setdefault("CRYPTOSCANNER_CMC_KEY", settings.cryptosscanner_cmc_key)

            from binance_data_provider import build_binance_dataframe

            df = build_binance_dataframe(limit=int(limit), max_workers=3)
            if df is not None and not df.empty:
                return df, "cmc"
        except Exception as exc:
            logger.info("CMC provider unavailable (%s); falling back to CoinGecko", exc)

    # 2. CoinGecko
    df = _fetch_coingecko(limit)
    return df, "coingecko"


# ══════════════════════════════════════════════════════════
# Strategy enrichment
# ══════════════════════════════════════════════════════════
def _enrich_with_strategy(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the project's existing signal engine to the DataFrame."""
    if df is None or df.empty:
        return df
    try:
        from analysis.signals import apply_strategy_to_df

        enriched = apply_strategy_to_df(
            df,
            strategy="composite",
            enrich=True,
            return_frame=True,
        )
        if isinstance(enriched, pd.DataFrame) and not enriched.empty:
            return enriched
        return df
    except Exception as exc:
        logger.exception("Strategy enrichment failed: %s", exc)
        return df


# ══════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════
def run_scan(persist_snapshot: bool = False) -> int:
    """
    Fetch market data, enrich with strategy, replace cache.

    Returns the number of rows cached (0 on failure).
    """
    try:
        df, source = _fetch_market_data(settings.scan_limit)
        if df is None or df.empty:
            with _LOCK:
                _CACHE["error"] = "no data returned by provider"
                _CACHE["source"] = source
            logger.warning("Scan produced no rows (source=%s)", source)
            return 0

        df = _enrich_with_strategy(df)

        # Sort by strongest signal score, then by 24h change
        if "Score" in df.columns:
            try:
                df = df.sort_values(
                    by=["Score", "24h Change (%)"],
                    ascending=[False, False],
                    na_position="last",
                )
            except Exception:
                pass
        elif "24h Change (%)" in df.columns:
            try:
                df = df.sort_values(
                    "24h Change (%)", ascending=False, na_position="last"
                )
            except Exception:
                pass

        df = df.reset_index(drop=True)
        records = _records_from_df(df)

        with _LOCK:
            _CACHE["rows"] = records
            _CACHE["updated_at"] = time.time()
            _CACHE["error"] = None
            _CACHE["source"] = source

        logger.info(
            "Scan complete | source=%s | rows=%d | limit=%d",
            source, len(records), settings.scan_limit,
        )

        if persist_snapshot:
            try:
                _persist_snapshot(records)
            except Exception as exc:
                logger.warning("Snapshot persist failed: %s", exc)

        return len(records)

    except Exception as exc:
        logger.exception("Scan failed: %s", exc)
        with _LOCK:
            _CACHE["error"] = str(exc)
        return 0


def _persist_snapshot(records: list[dict[str, Any]]) -> None:
    """Store a compact snapshot in `scan_snapshots` (audit / history)."""
    from server.models import ScanSnapshot

    db = SessionLocal()
    try:
        snap = ScanSnapshot(
            row_count=len(records),
            payload=json.dumps(records, default=str)[:5_000_000],  # cap at 5 MB
        )
        db.add(snap)
        db.commit()
    finally:
        db.close()


def get_symbol_price(symbol: str) -> Optional[float]:
    """Return the USD price for `symbol` from the current cache, if present."""
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