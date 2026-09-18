# server/services/prices.py
"""
Live-price service for paper SL/TP auto-close.

Strategy
--------
1. Read from the scanner cache (instant, no network).
2. For symbols not in the cache, hit CoinGecko directly.
3. Cache the symbol → slug mapping to avoid repeated lookups.

Public function:
    get_prices(["BTC", "ETH"]) → {"BTC": 50000.0, "ETH": 3000.0}
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Iterable, Optional

from server.services import scanner

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# Symbol → CoinGecko slug mapping (cached in-process)
# ══════════════════════════════════════════════════════════
_SLUG_LOCK = threading.RLock()
_SLUG_CACHE: dict[str, str] = {}
_SLUG_CACHE_TS: float = 0.0
_SLUG_CACHE_TTL = 3600.0  # 1 hour

# Static fallback for the most-traded coins
_STATIC_SLUGS: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "SOL": "solana",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "MATIC": "matic-network",
    "DOT": "polkadot",
    "LTC": "litecoin",
    "TRX": "tron",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "ATOM": "cosmos",
    "UNI": "uniswap",
    "SHIB": "shiba-inu",
    "XLM": "stellar",
    "NEAR": "near",
    "APT": "aptos",
    "ARB": "arbitrum",
    "OP": "optimism",
    "TON": "the-open-network",
    "PEPE": "pepe",
    "SUI": "sui",
}


def _refresh_slug_cache() -> None:
    """Rebuild the symbol → slug map from CoinGecko's /coins/list."""
    global _SLUG_CACHE, _SLUG_CACHE_TS

    try:
        from api.api_coingecko import CoinGeckoClient

        client = CoinGeckoClient()
        coins = client.get_coin_list()
        if not isinstance(coins, list):
            return

        mapping: dict[str, str] = dict(_STATIC_SLUGS)
        # Prefer entries with higher market presence. Since /coins/list
        # doesn't provide rank, we just take the first match per symbol,
        # and let static entries win for known coins.
        for c in coins:
            if not isinstance(c, dict):
                continue
            sym = str(c.get("symbol") or "").upper().strip()
            cid = c.get("id")
            if not sym or not cid:
                continue
            if sym not in mapping:
                mapping[sym] = str(cid)

        with _SLUG_LOCK:
            _SLUG_CACHE = mapping
            _SLUG_CACHE_TS = time.time()

        logger.info("Slug cache refreshed: %d entries", len(mapping))
    except Exception as exc:
        logger.warning("Could not refresh slug cache: %s", exc)


def _get_slug(symbol: str) -> Optional[str]:
    sym = symbol.upper().strip()
    if not sym:
        return None

    with _SLUG_LOCK:
        cache_age = time.time() - _SLUG_CACHE_TS
        if sym in _SLUG_CACHE:
            return _SLUG_CACHE[sym]
        need_refresh = (not _SLUG_CACHE) or (cache_age > _SLUG_CACHE_TTL)

    # Static first
    if sym in _STATIC_SLUGS:
        return _STATIC_SLUGS[sym]

    if need_refresh:
        _refresh_slug_cache()
        with _SLUG_LOCK:
            return _SLUG_CACHE.get(sym)

    return None


# ══════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════
def get_prices(symbols: Iterable[str]) -> dict[str, float]:
    """
    Return {symbol: usd_price} for the requested symbols.

    Missing symbols are simply omitted from the result.
    Never raises.
    """
    wanted = {str(s).upper().strip() for s in symbols if s}
    wanted.discard("")

    if not wanted:
        return {}

    out: dict[str, float] = {}

    # 1. Scanner cache
    cached = scanner.get_cached()
    for row in cached.get("rows", []):
        sym = str(row.get("Symbol") or "").upper()
        if sym in wanted and sym not in out:
            p = row.get("Price")
            if isinstance(p, (int, float)) and p > 0:
                out[sym] = float(p)

    missing = wanted - set(out.keys())
    if not missing:
        return out

    # 2. CoinGecko direct fetch
    _fetch_missing_prices(list(missing), out)
    return out


def _fetch_missing_prices(symbols: list[str], out: dict[str, float]) -> None:
    """Fill `out` in-place with prices fetched from CoinGecko."""
    if not symbols:
        return

    symbol_to_slug: dict[str, str] = {}
    for sym in symbols:
        slug = _get_slug(sym)
        if slug:
            symbol_to_slug[sym] = slug

    if not symbol_to_slug:
        logger.debug("No slugs resolved for %s", symbols)
        return

    try:
        from api.api_coingecko import CoinGeckoClient

        client = CoinGeckoClient()
        data = client.get_price(
            coin_ids=list(symbol_to_slug.values()),
            vs_currencies="usd",
        )
        if not isinstance(data, dict):
            return

        for sym, slug in symbol_to_slug.items():
            entry = data.get(slug)
            if not isinstance(entry, dict):
                continue
            p = entry.get("usd")
            if isinstance(p, (int, float)) and p > 0:
                out[sym] = float(p)

    except Exception as exc:
        logger.warning("CoinGecko price fetch failed for %s: %s", symbols, exc)


def get_price(symbol: str) -> Optional[float]:
    """Convenience wrapper for a single symbol."""
    prices = get_prices([symbol])
    return prices.get(symbol.upper())


__all__ = ["get_prices", "get_price"]