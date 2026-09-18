"""Global-lead momentum engine for Nobitex IRT execution.

CoinMarketCap supplies the global reference feed while Nobitex supplies the
local IRT order book and execution venue. The engine compares the two paths
to detect sustained global momentum before the local market fully reacts.

The engine never places orders. It keeps a coin when the *stored IRT
path* is still making a real upward move, then applies a short quality
filter on the book.

v7.1.1 — Balanced eagle exception:
- When BTC is dumping, the engine still allows entries on coins that
  make an exceptional, liquid, tight-spread move. Thresholds are tuned
  for a live IRT book: 2.5% observed, 2.0% 1h, 300M IRT volume,
  0.9% max spread.
- Two entry tiers:
    * Trend Buy  — normal path, no BTC dump.
    * Eagle Buy  — passes the exception while BTC is dumping.
- btc_dump_exception_enabled can be turned off in bot_config.json.
- Stats line includes btc_dump_exc for visibility.
"""
from __future__ import annotations

import datetime as _dt
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

from api.api_coinmarketcap import extract_usd_quote, iter_cmc_coins
from core.utils import safe_float


STABLES = {"USDT", "USDC", "USD", "DAI", "TUSD", "FDUSD", "USDE", "PYUSD"}
_BTC_PROXIES = {"BTC", "WBTC", "TBTC"}
_RECENT_SCANS = 2
_FADE_PCT = 0.25
_WIDE_SPREAD_EXTRA_PCT = 0.35
_DISABLED = -100.0   # sentinel: any filter set to this is treated as "off"


def _pct_change(new: float, old: float) -> Optional[float]:
    if old is None or new is None or old <= 0 or new <= 0:
        return None
    return (float(new) - float(old)) / float(old) * 100.0


def _parse_age_sec(updated: Any, now: float) -> float:
    if not updated:
        return 0.0
    try:
        text = str(updated).replace("Z", "+00:00")
        ts = _dt.datetime.fromisoformat(text).timestamp()
        return max(0.0, now - ts)
    except Exception:
        return 0.0


class GlobalLeadEngine:
    """Nobitex momentum engine. Name kept for backward compatibility."""

    def __init__(
        self,
        *,
        global_pump_pct: float = 1.2,
        max_spread_pct: float = 1.2,
        min_global_volume_usd: float = 500_000_000.0,   # IRT-denominated
        max_global_quote_age_sec: float = 300.0,
        min_local_volume_irt: float = 500_000_000.0,
        max_local_fall_pct: float = 0.8,
        max_chase_pct: float = 0.55,
        movement_lookback_scans: int = 4,
        min_confirm_scans: int = 1,
        min_observed_move_pct: float = 0.7,
        max_local_24h_pct: float = 15.0,
        min_global_24h_pct: float = _DISABLED,
        min_cmc_1h_pct: float = _DISABLED,
        min_volume_change_24h_pct: float = _DISABLED,
        btc_max_dump_pct: float = 1.0,
        history_len: int = 48,
        # ── Eagle exception (v7.1.1 — tuned for live IRT) ──
        btc_dump_exception_enabled: bool = True,
        eagle_min_observed_move_pct: float = 2.5,
        eagle_min_1h_pct: float = 2.0,
        eagle_min_volume_irt: float = 300_000_000.0,
        eagle_max_spread_pct: float = 0.9,
        **_ignored: Any,
    ):
        self.global_pump_pct = float(global_pump_pct)
        self.max_spread_pct = float(max_spread_pct)
        self.min_global_volume_usd = float(min_global_volume_usd)
        self.max_global_quote_age_sec = float(max_global_quote_age_sec)
        self.min_local_volume_irt = float(min_local_volume_irt)
        self.max_local_fall_pct = float(max_local_fall_pct)
        self.max_chase_pct = float(max_chase_pct)
        self.movement_lookback_scans = max(1, int(movement_lookback_scans or 1))
        self.min_confirm_scans = max(1, int(min_confirm_scans or 1))
        self.min_observed_move_pct = float(min_observed_move_pct)
        self.max_local_24h_pct = float(max_local_24h_pct)
        self.min_global_24h_pct = float(min_global_24h_pct)
        self.min_cmc_1h_pct = float(min_cmc_1h_pct)
        self.min_volume_change_24h_pct = float(min_volume_change_24h_pct)
        self.btc_max_dump_pct = float(btc_max_dump_pct)
        self.history_len = max(8, int(history_len or 48))
        self.last_stats: Dict[str, Any] = {}

        # Eagle exception
        self.btc_dump_exception_enabled = bool(btc_dump_exception_enabled)
        self.eagle_min_observed_move_pct = float(eagle_min_observed_move_pct)
        self.eagle_min_1h_pct = float(eagle_min_1h_pct)
        self.eagle_min_volume_irt = float(eagle_min_volume_irt)
        self.eagle_max_spread_pct = float(eagle_max_spread_pct)

        self._usd_history: Dict[str, Deque[Tuple[float, float]]] = defaultdict(self._new_history)
        self._irt_history: Dict[str, Deque[Tuple[float, float]]] = defaultdict(self._new_history)
        self._trend_first_seen: Dict[str, float] = {}
        self._trend_hits: Dict[str, int] = {}
        self._last_move: Dict[str, float] = {}

    def _new_history(self) -> Deque[Tuple[float, float]]:
        return deque(maxlen=self.history_len)

    def configure(self, **kwargs: Any) -> None:
        """Update filters without wiping observed-price history."""
        int_fields = {"movement_lookback_scans", "min_confirm_scans", "history_len"}
        bool_fields = {"btc_dump_exception_enabled"}
        skip = {
            "min_discount_pct", "max_discount_pct", "max_local_premium_pct",
        }
        for key, value in kwargs.items():
            if key in skip or not hasattr(self, key) or value is None:
                continue
            if key in bool_fields:
                setattr(self, key, bool(value))
            elif key in int_fields:
                setattr(self, key, max(1, int(value)))
            else:
                try:
                    setattr(self, key, type(getattr(self, key))(value))
                except (TypeError, ValueError):
                    pass

    def observed_lead_threshold(self) -> float:
        """Live observed move needed across lookback scans."""
        if self.min_observed_move_pct > 0:
            return max(0.5, float(self.min_observed_move_pct))
        return max(0.6, float(self.global_pump_pct) * 0.5)

    def stats_line(self) -> str:
        s = self.last_stats or {}
        return (
            "local={local} matched={matched} no_data={no_cmc} spread={spread} "
            "vol={volume} stale={stale} no_trend={no_trend} falling={falling} "
            "fading={fading} confirm={confirm} chase={chase} passed={passed} "
            "btc_dump={btc_dump} btc_dump_exc={btc_dump_exc} "
            "best_mom={best_1h} best_obs={best_obs}"
        ).format(**{k: s.get(k, 0) for k in (
            "local", "matched", "no_cmc", "spread", "volume", "stale",
            "no_trend", "falling", "fading", "confirm", "chase", "passed",
            "btc_dump", "btc_dump_exc", "best_1h", "best_obs",
        )})

    @staticmethod
    def usdt_irt_from_rows(local_rows: Iterable[Dict[str, Any]]) -> float:
        for local in local_rows or []:
            if str(local.get("Symbol") or "").upper() != "USDT":
                continue
            ask = safe_float(local.get("Ask")) or 0.0
            if ask > 0:
                return ask
            last = safe_float(local.get("Price")) or 0.0
            if last > 0:
                return last
        return 0.0

    @staticmethod
    def _cmc_rows(payload: Any) -> List[Dict[str, Any]]:
        return iter_cmc_coins(payload)

    def build_global_map(
        self,
        payload: Any,
        now: Optional[float] = None,
    ) -> Dict[str, Dict[str, Any]]:
        now = time.time() if now is None else float(now)
        out: Dict[str, Dict[str, Any]] = {}
        for coin in self._cmc_rows(payload):
            if not isinstance(coin, dict):
                continue
            symbol = str(coin.get("symbol") or "").upper().strip()
            if not symbol or symbol in STABLES:
                continue
            usd = extract_usd_quote(coin)
            if not usd:
                continue
            price = safe_float(usd.get("price")) or 0.0
            if price <= 0:
                continue
            change_1h = safe_float(usd.get("percent_change_1h")) or 0.0
            volume = safe_float(usd.get("volume_24h")) or 0.0
            market_cap = safe_float(usd.get("market_cap")) or 0.0
            updated = usd.get("last_updated") or coin.get("last_updated")
            row = {
                "GlobalSymbol": symbol,
                "GlobalName": coin.get("name") or symbol,
                "GlobalPriceUSD": price,
                "Global1hPct": change_1h,
                "Global24hPct": safe_float(usd.get("percent_change_24h")) or 0.0,
                "Global7dPct": safe_float(usd.get("percent_change_7d")) or 0.0,
                "GlobalVolumeUSD": volume,
                "GlobalVolumeChange24hPct": safe_float(usd.get("volume_change_24h")) or 0.0,
                "GlobalMarketCapUSD": market_cap,
                "GlobalRank": coin.get("cmc_rank") or coin.get("cmcRank"),
                "GlobalCMCId": coin.get("id"),
                "GlobalUpdatedAgeSec": _parse_age_sec(updated, now),
            }
            old = out.get(symbol)
            if old is None or row["GlobalMarketCapUSD"] > old["GlobalMarketCapUSD"]:
                out[symbol] = row
        return out

    def _lookback_move(
        self,
        history: Deque[Tuple[float, float]],
        current: float,
        lookback: int,
    ) -> Optional[float]:
        if current <= 0 or not history:
            return None
        want = max(1, int(lookback or 1))
        have = len(history)
        if have >= want:
            baseline = float(history[-want][1])
        else:
            min_partial = 3 if want >= 3 else want
            if have < min_partial:
                return None
            baseline = float(history[0][1])
        return _pct_change(current, baseline)

    def _record(self, symbol: str, usd: float, irt: float, now: float) -> None:
        if usd > 0:
            self._usd_history[symbol].append((now, usd))
        if irt > 0:
            self._irt_history[symbol].append((now, irt))

    def _prune_trends(self, live_symbols: Iterable[str]) -> None:
        live = {str(s).upper() for s in live_symbols}
        for store in (self._trend_first_seen, self._trend_hits, self._last_move):
            for symbol in list(store):
                if symbol not in live:
                    store.pop(symbol, None)

    def _btc_is_dumping(
        self,
        global_map: Dict[str, Dict[str, Any]],
        now: float,
    ) -> bool:
        if self.btc_max_dump_pct <= 0:
            return False
        btc = global_map.get("BTC")
        if not btc:
            return False
        cmc_1h = float(btc.get("Global1hPct") or 0.0)
        observed = self._lookback_move(
            self._usd_history["BTC"],
            float(btc.get("GlobalPriceUSD") or 0.0),
            self.movement_lookback_scans,
        )
        worst = cmc_1h
        if observed is not None:
            worst = min(worst, observed)
        return worst <= -abs(self.btc_max_dump_pct)

    def _chase_pct(self, ask: float, last: float) -> float:
        if ask <= 0 or last <= 0 or ask <= last:
            return 0.0
        return (ask - last) / last * 100.0

    def _is_eagle_exception(
        self,
        *,
        observed_global: Optional[float],
        cmc_1h: float,
        volume_irt: float,
        spread: float,
        chase_pct: float,
        local_24h: float,
    ) -> bool:
        """Return True if the coin qualifies as a strong mover even when
        BTC is dumping. Strict by design: only coins with real, liquid,
        tight-spread moves pass."""
        if not self.btc_dump_exception_enabled:
            return False
        if observed_global is None:
            return False
        if observed_global < self.eagle_min_observed_move_pct:
            return False
        if cmc_1h < self.eagle_min_1h_pct:
            return False
        if volume_irt < self.eagle_min_volume_irt:
            return False
        if spread > self.eagle_max_spread_pct:
            return False
        if chase_pct > self.max_chase_pct:
            return False
        # Reject parabolic spikes: don't buy after a huge 24h move.
        if self.max_local_24h_pct > 0 and local_24h > self.max_local_24h_pct:
            return False
        return True

    def _score(
        self,
        *,
        global_1h: float,
        observed_global: Optional[float],
        spread: float,
        volume_usd: float,
        volume_change: float,
        local_tick: float,
        hold_scans: int,
    ) -> float:
        live_move = observed_global if observed_global is not None else global_1h
        score = (
            min(max(live_move, 0.0), 15.0) * 14.0
            + min(max(global_1h, 0.0), 15.0) * 5.0
            + min(volume_usd / max(self.min_global_volume_usd, 1.0), 20.0)
            + min(max(volume_change, 0.0), 40.0) * 0.15
            + min(max(local_tick, 0.0), 2.0) * 6.0
            + min(max(hold_scans, 1), 6) * 1.5
            - spread * 6.0
        )
        if observed_global is not None and observed_global < 0:
            score -= 25.0
        return score

    def _reset_hit(self, symbol: str) -> None:
        self._trend_hits.pop(symbol, None)
        self._trend_first_seen.pop(symbol, None)

    def evaluate(
        self,
        local_rows: List[Dict[str, Any]],
        global_payload: Any,
        usdt_irt: float = 0.0,
        *,
        now: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        now = time.time() if now is None else float(now)
        stats: Dict[str, Any] = {
            "local": 0, "matched": 0, "no_cmc": 0, "no_ask": 0, "volume": 0,
            "spread": 0, "stale": 0, "no_trend": 0, "no_lead": 0, "falling": 0,
            "fading": 0, "local_fall": 0, "chase": 0, "cmc1h": 0,
            "btc_dump": 0, "btc_dump_exc": 0, "local_24h": 0,
            "global_24h": 0, "vol_chg": 0,
            "confirm": 0, "passed": 0, "btc_dumping": False,
            "best_1h": 0.0, "best_obs": 0.0,
        }
        fx = float(usdt_irt or 0.0)
        global_map = self.build_global_map(global_payload, now=now)
        lookback = self.movement_lookback_scans
        obs_need = self.observed_lead_threshold()
        btc_dumping = self._btc_is_dumping(global_map, now)
        stats["btc_dumping"] = btc_dumping
        live_symbols = []
        candidates: List[Dict[str, Any]] = []
        best_1h = 0.0
        best_obs = 0.0

        cmc_1h_enabled = self.min_cmc_1h_pct > _DISABLED / 2
        global_24h_enabled = self.min_global_24h_pct > _DISABLED / 2
        vol_chg_enabled = self.min_volume_change_24h_pct > _DISABLED / 2

        for local in local_rows or []:
            symbol = str(local.get("Symbol") or "").upper().strip()
            if not symbol or symbol in STABLES:
                continue
            stats["local"] += 1
            live_symbols.append(symbol)
            g = global_map.get(symbol)
            ask = safe_float(local.get("Ask")) or 0.0
            last = safe_float(local.get("Price")) or 0.0
            usd = float(g["GlobalPriceUSD"]) if g else 0.0

            observed_global = (
                self._lookback_move(self._usd_history[symbol], usd, lookback)
                if g else None
            )
            observed_local = self._lookback_move(
                self._irt_history[symbol], ask or last, lookback
            )
            recent_global = (
                self._lookback_move(self._usd_history[symbol], usd, _RECENT_SCANS)
                if g else None
            )
            if g:
                self._record(symbol, usd, ask or last, now)
            else:
                stats["no_cmc"] += 1
                if ask > 0 or last > 0:
                    self._record(symbol, 0.0, ask or last, now)
                continue

            stats["matched"] += 1
            cmc_1h = float(g["Global1hPct"] or 0.0)
            best_1h = max(best_1h, cmc_1h)
            if observed_global is not None:
                best_obs = max(best_obs, float(observed_global))

            if ask <= 0:
                stats["no_ask"] += 1
                continue
            bid = safe_float(local.get("Bid")) or 0.0
            volume_irt = safe_float(local.get("Volume")) or 0.0
            if bid <= 0 or volume_irt < self.min_local_volume_irt:
                stats["volume"] += 1
                continue
            spread = (ask - bid) / bid * 100.0 if ask >= bid else 99.0
            if spread > self.max_spread_pct + _WIDE_SPREAD_EXTRA_PCT:
                stats["spread"] += 1
                continue
            if g["GlobalVolumeUSD"] < self.min_global_volume_usd:
                stats["volume"] += 1
                continue
            if g["GlobalUpdatedAgeSec"] > self.max_global_quote_age_sec:
                stats["stale"] += 1
                continue
            if global_24h_enabled and g["Global24hPct"] < self.min_global_24h_pct:
                stats["global_24h"] += 1
                continue
            if vol_chg_enabled and g["GlobalVolumeChange24hPct"] < self.min_volume_change_24h_pct:
                stats["vol_chg"] += 1
                continue

            # Optional: reject coins whose momentum reading is falling.
            if cmc_1h_enabled and cmc_1h < self.min_cmc_1h_pct:
                stats["cmc1h"] += 1
                self._reset_hit(symbol)
                continue

            local_24h = safe_float(local.get("24h Change (%)")) or 0.0
            if self.max_local_24h_pct > 0 and local_24h > self.max_local_24h_pct:
                stats["local_24h"] += 1
                continue

            chase_pct = self._chase_pct(ask, last)

            # ── BTC dump guard with eagle exception ──
            eagle_entry = False
            if btc_dumping and symbol not in _BTC_PROXIES:
                eagle_ok = self._is_eagle_exception(
                    observed_global=observed_global,
                    cmc_1h=cmc_1h,
                    volume_irt=volume_irt,
                    spread=spread,
                    chase_pct=chase_pct,
                    local_24h=local_24h,
                )
                if not eagle_ok:
                    stats["btc_dump"] += 1
                    continue
                stats["btc_dump_exc"] += 1
                eagle_entry = True

            # Require a measured path, not just a green print.
            if observed_global is None or observed_global < obs_need:
                stats["no_trend"] += 1
                stats["no_lead"] += 1
                self._reset_hit(symbol)
                continue

            live_move = observed_global
            if spread > self.max_spread_pct:
                local_ok = observed_local is not None and observed_local >= 0.35
                if not (local_ok and live_move >= max(obs_need + 0.5, 1.4)):
                    stats["spread"] += 1
                    continue

            if observed_global is not None and observed_global < -_FADE_PCT:
                stats["falling"] += 1
                self._reset_hit(symbol)
                continue

            if recent_global is not None and recent_global < -_FADE_PCT:
                stats["fading"] += 1
                self._reset_hit(symbol)
                continue

            local_tick = safe_float(local.get("Nobitex 30s Change (%)")) or 0.0
            if local_tick < -self.max_local_fall_pct:
                stats["local_fall"] += 1
                self._reset_hit(symbol)
                continue
            if observed_local is not None and observed_local < -0.40:
                stats["local_fall"] += 1
                self._reset_hit(symbol)
                continue

            if chase_pct > self.max_chase_pct:
                stats["chase"] += 1
                continue

            hits = self._trend_hits.get(symbol, 0) + 1
            self._trend_hits[symbol] = hits
            first = self._trend_first_seen.get(symbol)
            if first is None:
                self._trend_first_seen[symbol] = now
                first = now
            self._last_move[symbol] = live_move
            strong_now = (
                (observed_global is not None and observed_global >= max(obs_need, 1.5))
                or cmc_1h >= max(self.global_pump_pct, 2.0)
                or eagle_entry
            )
            need_hits = 1 if strong_now else self.min_confirm_scans
            if hits < need_hits:
                stats["confirm"] += 1
                continue

            hold_seconds = max(0.0, now - first)
            score = self._score(
                global_1h=cmc_1h,
                observed_global=observed_global,
                spread=spread,
                volume_usd=g["GlobalVolumeUSD"],
                volume_change=g["GlobalVolumeChange24hPct"],
                local_tick=local_tick,
                hold_scans=hits,
            )
            stats["passed"] += 1
            fair_irt = (g["GlobalPriceUSD"] * fx) if fx > 0 else 0.0

            signal_tag = "Eagle Buy" if eagle_entry else "Trend Buy"

            candidates.append({
                **local,
                **g,
                "USDT/IRT": fx,
                "Fair IRT Price": fair_irt,
                "Nobitex Ask": ask,
                "Nobitex Bid": bid,
                "Nobitex Spread (%)": spread,
                "TrendHold (sec)": hold_seconds,
                "Lag Duration (sec)": hold_seconds,
                "ObservedGlobalMove (%)": observed_global if observed_global is not None else 0.0,
                "ObservedLocalMove (%)": observed_local if observed_local is not None else 0.0,
                "LiveLeadMove (%)": live_move,
                "GapConfirmScans": hits,
                "TrendConfirmScans": hits,
                "GlobalLeadScore": score,
                "pump_pct": live_move,
                "Signal": f"{signal_tag} {live_move:+.2f}%",
                "DataSource": "Nobitex",
                "ExecutionVenue": "Nobitex",
                "EagleException": bool(eagle_entry),
            })

        self._prune_trends(live_symbols)
        candidates.sort(key=lambda x: float(x.get("GlobalLeadScore", 0.0)), reverse=True)
        stats["best_1h"] = round(best_1h, 2)
        stats["best_obs"] = round(best_obs, 2)
        self.last_stats = stats
        return candidates