# trading/bot_config.py
"""
BotConfig v7.0.0 — Eagle Exception support.

Changes vs v6.4.0:
- Added 5 Eagle Exception fields:
    btc_dump_exception_enabled, eagle_min_observed_move_pct,
    eagle_min_1h_pct, eagle_min_volume_irt, eagle_max_spread_pct.
- STRATEGY_DEFAULTS_VERSION bumped to 8.
- EAGLE_DEFAULTS migration added so existing installs pick up the new
  defaults on next load.
- Eagle fields are validated in from_dict and persisted in to_dict.
"""
from __future__ import annotations
import os
import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict
from cryptography.fernet import Fernet, InvalidToken

from core.config import APPDATA_DIR
from trading.execution_mode import PAPER, normalize_execution_mode

logger = logging.getLogger(__name__)

_SOURCE_DIR = Path(__file__).resolve().parent.parent

DEFAULT_FIXED_POSITION_TOMAN = 1_000_000.0
# Exchange quote is Rial/RLS for Nobitex. The user-facing amount is Toman.
DEFAULT_FIXED_POSITION_QUOTE = 10_000_000.0
DEFAULT_MAX_NOTIONAL_QUOTE = 15_000_000.0
DEFAULT_MIN_NOTIONAL_QUOTE = 10_000_000.0
DEFAULT_MAX_POSITION_PCT = 20.0
DEFAULT_MAX_TOTAL_EXPOSURE_PCT = 60.0
STRATEGY_DEFAULTS_VERSION = 10

DEFAULT_CONFIG_FILE = os.path.join(APPDATA_DIR, "bot_config.json")
_CREDENTIALS_FILE = os.path.join(APPDATA_DIR, "nobitex_credentials.enc")
_CREDENTIAL_KEY_FILE = os.path.join(APPDATA_DIR, "nobitex_credentials.key")
_CONFIG_LOCK = threading.Lock()


def _resolve_config_path(path: str) -> str:
    if not path:
        return DEFAULT_CONFIG_FILE
    if os.path.isabs(path):
        return path
    return os.path.join(APPDATA_DIR, path)


def _credential_key() -> bytes:
    os.makedirs(APPDATA_DIR, exist_ok=True)
    if os.path.exists(_CREDENTIAL_KEY_FILE):
        try:
            key = Path(_CREDENTIAL_KEY_FILE).read_bytes().strip()
            Fernet(key)
            return key
        except Exception:
            logger.warning("Nobitex credential key is invalid; generating a new one.")
    key = Fernet.generate_key()
    Path(_CREDENTIAL_KEY_FILE).write_bytes(key)
    return key


def _load_credentials() -> tuple[str, str]:
    try:
        if not os.path.exists(_CREDENTIALS_FILE):
            return "", ""
        payload = Fernet(_credential_key()).decrypt(Path(_CREDENTIALS_FILE).read_bytes())
        data = json.loads(payload.decode("utf-8"))
        return str(data.get("api_key", "") or ""), str(data.get("api_secret", "") or "")
    except (InvalidToken, ValueError, OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load stored exchange credentials: %s", exc)
        return "", ""


def _save_credentials(api_key: str, api_secret: str) -> None:
    os.makedirs(APPDATA_DIR, exist_ok=True)
    if not api_key and not api_secret:
        try:
            if os.path.exists(_CREDENTIALS_FILE):
                os.remove(_CREDENTIALS_FILE)
        except OSError:
            pass
        return
    payload = json.dumps({"api_key": api_key or "", "api_secret": api_secret or ""}).encode("utf-8")
    encrypted = Fernet(_credential_key()).encrypt(payload)
    tmp = f"{_CREDENTIALS_FILE}.{uuid.uuid4().hex[:8]}.tmp"
    Path(tmp).write_bytes(encrypted)
    os.replace(tmp, _CREDENTIALS_FILE)


@dataclass
class BotConfig:
    # ── Exchange ─────────────────────────────────────────────
    exchange: str = "simulator"
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = False
    spot_mode: bool = True

    # ── Nobitex Specific ────────────────────────────────────
    nobitex_market: str = "IRT"

    # ── Trading Pairs / Strategy ─────────────────────────────
    trading_pairs: List[str] = field(default_factory=lambda: ["BTCIRT", "ETHIRT"])
    quote_currency: str = "IRT"
    quote_unit: str = "rial"
    display_currency: str = "TOMAN"
    display_position_size_toman: float = DEFAULT_FIXED_POSITION_TOMAN
    candle_interval: str = "15m"
    kline_limit: int = 120

    # ── Account / Risk ───────────────────────────────────────
    account_balance: float = 1000.0
    risk_per_trade_pct: float = 1.0
    max_open_positions: int = 3
    max_drawdown_percent: float = 15.0
    halt_on_max_drawdown: bool = True

    # ── Pure Price Action / Real Movement Strategy ──────────
    pump_threshold_pct: float = 3.0
    movement_lookback_scans: int = 4
    stop_loss_pct: float = 3.0
    trailing_distance_pct: float = 3.0
    trailing_activation_pct: float = 3.0
    trailing_stop_enabled: bool = True
    take_profit_percent: float = 50.0

    # ── Position Sizing ──────────────────────────────────────
    position_size_mode: str = "fixed"
    fixed_position_quote: float = DEFAULT_FIXED_POSITION_QUOTE
    # User-facing fixed size. For Nobitex IRT, 1,000,000 Toman = 10,000,000 Rial.
    fixed_position_toman: float = DEFAULT_FIXED_POSITION_TOMAN
    quantity_step: float = 0.00000001
    min_quantity: float = 0.0
    fee_buffer_pct: float = 0.25
    max_position_pct: float = 90.0
    capital_usage_pct: float = 90.0
    min_notional_quote: float = DEFAULT_MIN_NOTIONAL_QUOTE
    max_notional_quote: float = DEFAULT_MAX_NOTIONAL_QUOTE
    max_total_exposure_pct: float = DEFAULT_MAX_TOTAL_EXPOSURE_PCT

    # ── Filters ─────────────────────────────────────────────
    min_volume_24h: float = 500_000.0
    min_market_cap: float = 0.0

    # ── Cooldowns ────────────────────────────────────────────
    cooldown_after_loss_min: int = 60
    cooldown_after_win_min: int = 15
    entry_cooldown_seconds: int = 900

    # ── Automation / Notifications ───────────────────────────
    check_interval_seconds: int = 20
    enable_auto_trading: bool = True
    execution_mode: str = PAPER
    trading_fee_pct: float = 0.1
    max_new_entries_per_cycle: int = 1

    # ── Entry confirmation ───────────────────────────────────
    confirmation_enabled: bool = True
    confirmation_pct: float = 0.4
    confirmation_max_minutes: int = 5
    invalidation_pct: float = 0.8
    max_chase_pct: float = 0.7
    min_quality: float = 0.4
    blocked_risk_levels: List[str] = field(default_factory=lambda: ["High", "Extreme"])
    reverse_signal_exit_enabled: bool = True
    use_risk_filter: bool = True

    # ── Real-movement trend follow (Nobitex-only) ────────────
    strategy: str = "nobitex_momentum"
    global_signal_source: str = "Nobitex"
    global_pump_threshold_pct: float = 2.0
    min_nobitex_discount_pct: float = 0.0
    max_nobitex_discount_pct: float = 18.0
    max_nobitex_spread_pct: float = 1.0
    min_global_volume_usd: float = 500_000.0
    max_global_quote_age_sec: float = 300.0
    max_local_fall_pct: float = 0.5
    global_scan_limit: int = 500
    min_confirm_scans: int = 1
    min_observed_move_pct: float = 3.0
    min_cmc_1h_pct: float = -100.0
    max_local_24h_pct: float = 15.0
    min_global_24h_pct: float = -100.0
    min_volume_change_24h_pct: float = -100.0
    btc_max_dump_pct: float = 2.5
    cmc_listings_ttl_sec: float = 300.0
    min_ask_depth_quote: float = 3_000_000.0
    max_local_premium_pct: float = 0.0

    # ── Eagle Exception (BTC dump bypass) ────────────────────
    btc_dump_exception_enabled: bool = True
    eagle_min_observed_move_pct: float = 2.5
    eagle_min_1h_pct: float = 2.0
    eagle_min_volume_irt: float = 300_000_000.0
    eagle_max_spread_pct: float = 0.9

    strategy_defaults_version: int = STRATEGY_DEFAULTS_VERSION

    # ── File Paths ───────────────────────────────────────────
    trade_log_file: str = field(default_factory=lambda: os.path.join(APPDATA_DIR, "trade_history.json"))
    config_file: str = field(default_factory=lambda: DEFAULT_CONFIG_FILE)

    # ── Alias Properties ─────────────────────────────────────
    @property
    def stop_loss_percent(self): return self.stop_loss_pct
    @stop_loss_percent.setter
    def stop_loss_percent(self, v): self.stop_loss_pct = float(v)

    @property
    def risk_per_trade(self): return self.risk_per_trade_pct
    @risk_per_trade.setter
    def risk_per_trade(self, v): self.risk_per_trade_pct = float(v)

    @property
    def max_positions(self): return self.max_open_positions
    @max_positions.setter
    def max_positions(self, v): self.max_open_positions = int(v)

    @property
    def take_profit_pct(self): return self.take_profit_percent
    @take_profit_pct.setter
    def take_profit_pct(self, v): self.take_profit_percent = float(v)

    @property
    def max_daily_loss(self): return self.max_drawdown_percent
    @max_daily_loss.setter
    def max_daily_loss(self, v): self.max_drawdown_percent = float(v)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("config_file", None)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BotConfig":
        if not data:
            return cls()
        d = dict(data)
        _ALIASES = {
            "stop_loss_percent": "stop_loss_pct",
            "risk_per_trade": "risk_per_trade_pct",
            "max_positions": "max_open_positions",
            "max_open_trades": "max_open_positions",
            "take_profit_pct": "take_profit_percent",
            "max_daily_loss": "max_drawdown_percent",
        }
        for alias, real in _ALIASES.items():
            if alias in d:
                if real in d:
                    d.pop(alias, None)
                else:
                    d[real] = d.pop(alias)

        valid = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in valid}

        _int_fields = {
            "max_open_positions", "kline_limit", "movement_lookback_scans",
            "check_interval_seconds", "entry_cooldown_seconds",
            "cooldown_after_loss_min", "cooldown_after_win_min",
            "max_new_entries_per_cycle", "confirmation_max_minutes",
            "global_scan_limit", "min_confirm_scans", "strategy_defaults_version",
        }
        _float_fields = {
            "account_balance", "risk_per_trade_pct", "stop_loss_pct",
            "max_drawdown_percent", "fixed_position_quote", "fixed_position_toman", "display_position_size_toman", "quantity_step", "min_quantity", "fee_buffer_pct", "max_position_pct",
            "min_notional_quote", "max_notional_quote", "max_total_exposure_pct",
            "min_volume_24h", "min_market_cap", "pump_threshold_pct",
            "trailing_distance_pct", "trailing_activation_pct",
            "take_profit_percent", "trading_fee_pct", "confirmation_pct",
            "invalidation_pct", "max_chase_pct", "min_quality",
            "global_pump_threshold_pct", "min_nobitex_discount_pct",
            "max_nobitex_discount_pct", "max_nobitex_spread_pct",
            "min_global_volume_usd", "max_global_quote_age_sec",
            "max_local_fall_pct", "min_observed_move_pct", "min_cmc_1h_pct",
            "max_local_24h_pct", "min_global_24h_pct",
            "min_volume_change_24h_pct", "btc_max_dump_pct",
            "cmc_listings_ttl_sec", "min_ask_depth_quote", "max_local_premium_pct",
            "eagle_min_observed_move_pct", "eagle_min_1h_pct",
            "eagle_min_volume_irt", "eagle_max_spread_pct",
        }
        _bool_fields = {
            "testnet", "spot_mode", "halt_on_max_drawdown", "enable_auto_trading",
            "trailing_stop_enabled", "confirmation_enabled",
            "reverse_signal_exit_enabled", "use_risk_filter",
            "btc_dump_exception_enabled",
        }

        for k in _int_fields:
            if k in filtered:
                try:
                    filtered[k] = int(filtered[k])
                except Exception:
                    pass
        for k in _float_fields:
            if k in filtered:
                try:
                    filtered[k] = float(filtered[k])
                except Exception:
                    pass
        for k in _bool_fields:
            if k in filtered:
                v = filtered[k]
                if isinstance(v, str):
                    filtered[k] = v.strip().lower() in ("1", "true", "yes", "on")
                else:
                    filtered[k] = bool(v)
        if "blocked_risk_levels" in filtered and not isinstance(filtered["blocked_risk_levels"], list):
            raw = str(filtered["blocked_risk_levels"] or "")
            filtered["blocked_risk_levels"] = [x.strip() for x in raw.split(",") if x.strip()]
        if "execution_mode" in filtered:
            filtered["execution_mode"] = normalize_execution_mode(filtered["execution_mode"])

        return cls(**filtered)


_BOTCONFIG_INIT = BotConfig.__init__
_BOTCONFIG_INIT_ALIASES = {
    "risk_per_trade": "risk_per_trade_pct",
    "stop_loss_percent": "stop_loss_pct",
    "take_profit_pct": "take_profit_percent",
    "max_positions": "max_open_positions",
    "max_daily_loss": "max_drawdown_percent",
    "max_open_trades": "max_open_positions",
}


def _botconfig_init(self, *args, **kwargs):
    for alias, real in _BOTCONFIG_INIT_ALIASES.items():
        if alias in kwargs:
            if real not in kwargs:
                kwargs[real] = kwargs.pop(alias)
            else:
                kwargs.pop(alias, None)
    _BOTCONFIG_INIT(self, *args, **kwargs)


BotConfig.__init__ = _botconfig_init  # type: ignore[method-assign]


TREND_EV_DEFAULTS: Dict[str, Any] = {
    "strategy_defaults_version": 2,
    "global_pump_threshold_pct": 1.5,
    "min_observed_move_pct": 1.0,
    "max_nobitex_spread_pct": 1.0,
    "min_global_volume_usd": 500_000.0,
    "min_volume_24h": 500_000.0,
    "max_chase_pct": 0.7,
    "btc_max_dump_pct": 0.7,
    "max_local_24h_pct": 8.0,
    "movement_lookback_scans": 4,
    "min_confirm_scans": 1,
    "stop_loss_pct": 2.0,
    "trailing_distance_pct": 1.5,
    "trailing_activation_pct": 0.5,
    "take_profit_percent": 6.0,
    "confirmation_enabled": True,
    "max_open_positions": 3,
    "max_new_entries_per_cycle": 1,
    "cooldown_after_win_min": 15,
}

POSITION_SIZE_DEFAULTS: Dict[str, Any] = {
    "strategy_defaults_version": 3,
    "position_size_mode": "fixed",
    "fixed_position_quote": DEFAULT_FIXED_POSITION_QUOTE,
    "max_notional_quote": DEFAULT_MAX_NOTIONAL_QUOTE,
    "min_notional_quote": DEFAULT_MIN_NOTIONAL_QUOTE,
    "max_position_pct": DEFAULT_MAX_POSITION_PCT,
    "max_total_exposure_pct": DEFAULT_MAX_TOTAL_EXPOSURE_PCT,
}

EARLY_TREND_DEFAULTS: Dict[str, Any] = {
    "strategy_defaults_version": 4,
    "global_pump_threshold_pct": 1.5,
    "min_observed_move_pct": 1.0,
    "max_nobitex_spread_pct": 1.0,
    "movement_lookback_scans": 4,
    "min_confirm_scans": 1,
    "stop_loss_pct": 2.0,
    "trailing_distance_pct": 1.5,
    "trailing_activation_pct": 0.5,
    "take_profit_percent": 6.0,
    "max_chase_pct": 0.7,
    "max_local_24h_pct": 8.0,
    "confirmation_enabled": True,
    "entry_cooldown_seconds": 900,
    "check_interval_seconds": 20,
}

WINNER_LOCK_DEFAULTS: Dict[str, Any] = {
    "strategy_defaults_version": 5,
    "trailing_distance_pct": 1.5,
    "trailing_activation_pct": 0.5,
}

PROFITABILITY_DEFAULTS: Dict[str, Any] = {
    "strategy_defaults_version": 6,
    "min_cmc_1h_pct": 0.0,
    "min_global_24h_pct": -2.0,
    "btc_max_dump_pct": 0.7,
    "risk_per_trade_pct": 1.0,
    "max_drawdown_percent": 15.0,
    "max_open_positions": 3,
    "min_notional_quote": DEFAULT_MIN_NOTIONAL_QUOTE,
    "max_notional_quote": DEFAULT_MAX_NOTIONAL_QUOTE,
    "use_risk_filter": True,
    "min_ask_depth_quote": 5_000_000.0,
    "cooldown_after_loss_min": 60,
}

# v7 — Nobitex-only strategy rebrand.
NOBITEX_ONLY_DEFAULTS: Dict[str, Any] = {
    "strategy_defaults_version": 7,
    "strategy": "nobitex_momentum",
    "global_signal_source": "Nobitex",
    "global_pump_threshold_pct": 0.0,
    "min_nobitex_discount_pct": 0.0,
    "max_nobitex_discount_pct": 0.0,
    "min_cmc_1h_pct": -100.0,
    "min_global_24h_pct": -100.0,
    "min_volume_change_24h_pct": -100.0,
    "cmc_listings_ttl_sec": 300.0,
}

# v9 — Unified Toman sizing and centralized money-unit boundary.
MONEY_SIZING_DEFAULTS: Dict[str, Any] = {
    "strategy_defaults_version": 10,
    "display_currency": "TOMAN",
    "display_position_size_toman": 1_000_000.0,
    "fixed_position_toman": 1_000_000.0,
    "fixed_position_quote": 10_000_000.0,
    "quantity_step": 0.00000001,
    "min_quantity": 0.0,
    "fee_buffer_pct": 0.25,
    "min_notional_quote": 10_000_000.0,
    "max_notional_quote": 15_000_000.0,
    "max_position_pct": 90.0,
    "capital_usage_pct": 90.0,
    "max_total_exposure_pct": 90.0,
}

# v8 — Eagle Exception: allow strong movers through when BTC dumps.
UNIFIED_EXECUTION_DEFAULTS: Dict[str, Any] = {
    "strategy_defaults_version": 10,
    "stop_loss_pct": 3.0,
    "trailing_distance_pct": 3.0,
    "trailing_activation_pct": 3.0,
    "take_profit_percent": 50.0,
    "capital_usage_pct": 90.0,
    "max_position_pct": 90.0,
    "max_total_exposure_pct": 90.0,
    "btc_dump_exception_enabled": False,
    "global_pump_threshold_pct": 0.0,
    "min_nobitex_discount_pct": 0.0,
    "max_nobitex_discount_pct": 0.0,
}

EAGLE_DEFAULTS: Dict[str, Any] = {
    "strategy_defaults_version": 8,
    "btc_dump_exception_enabled": False,
    "eagle_min_observed_move_pct": 2.5,
    "eagle_min_1h_pct": 2.0,
    "eagle_min_volume_irt": 300_000_000.0,
    "eagle_max_spread_pct": 0.9,
    "btc_max_dump_pct": 2.5,
}


def load_config(file_path: str = DEFAULT_CONFIG_FILE) -> BotConfig:
    path = _resolve_config_path(file_path)
    if not os.path.exists(path):
        cfg = BotConfig()
        cfg.config_file = path
        return cfg

    with _CONFIG_LOCK:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}

            stored_key, stored_secret = _load_credentials()
            if stored_key or stored_secret:
                data["api_key"] = stored_key
                data["api_secret"] = stored_secret
            else:
                data["api_key"] = ""
                data["api_secret"] = ""

            data.pop("api_key_encrypted", None)
            data.pop("api_secret_encrypted", None)

            version = int(data.get("strategy_defaults_version") or 0)
            if version < 2:
                data.update(TREND_EV_DEFAULTS)
            if version < 3:
                data.update(POSITION_SIZE_DEFAULTS)
            if version < 4:
                data.update(EARLY_TREND_DEFAULTS)
            if version < 5:
                data.update(WINNER_LOCK_DEFAULTS)
            if version < 6:
                data.update(PROFITABILITY_DEFAULTS)
            if version < 7:
                data.update(NOBITEX_ONLY_DEFAULTS)
            if version < 8:
                for k, v in EAGLE_DEFAULTS.items():
                    data.setdefault(k, v)
            if version < 9:
                for k, v in MONEY_SIZING_DEFAULTS.items():
                    data.setdefault(k, v)
            if version < 10:
                for k, v in UNIFIED_EXECUTION_DEFAULTS.items():
                    data[k] = v

            cfg = BotConfig.from_dict(data)
            cfg.execution_mode = normalize_execution_mode(getattr(cfg, "execution_mode", PAPER))
            if str(cfg.exchange).strip().lower() == "nobitex":
                market = str(getattr(cfg, "nobitex_market", "") or "").strip().upper()
                quote = str(getattr(cfg, "quote_currency", "") or "").strip().upper()
                if market in ("", "RLS"):
                    market = "IRT"
                if market == "USDT" and quote == "USDT":
                    market = "IRT"
                    quote = "IRT"
                elif market == "IRT":
                    quote = "IRT"
                cfg.nobitex_market = market
                cfg.quote_currency = quote
                # Keep the user-facing Toman amount and exchange Rial amount in sync.
                # Never let an old 5M/6M Rial setting override the explicit Toman size.
                if "fixed_position_toman" in data:
                    cfg.fixed_position_quote = float(cfg.fixed_position_toman) * 10.0
                    cfg.display_position_size_toman = float(cfg.fixed_position_toman)
            cfg.config_file = path
            return cfg
        except Exception as e:
            logger.error("Error loading bot config (%s): %s — using defaults.", path, e)
            return BotConfig()


def save_config(config: BotConfig, file_path: str = DEFAULT_CONFIG_FILE) -> bool:
    cfg = config or BotConfig()
    path = _resolve_config_path(file_path)

    with _CONFIG_LOCK:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = cfg.to_dict()
            _save_credentials(str(data.pop("api_key", "") or ""), str(data.pop("api_secret", "") or ""))

            tmp_name = f"{path}.{threading.get_ident()}.{uuid.uuid4().hex[:6]}.tmp"
            with open(tmp_name, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
            return True
        except Exception as e:
            logger.error("Error saving bot config (%s): %s", path, e)
            return False


def validate_config(config: BotConfig) -> List[str]:
    errors: List[str] = []
    c = config or BotConfig()

    if not (0 < c.risk_per_trade_pct <= 100):
        errors.append("risk_per_trade_pct must be between 0 and 100.")
    if c.max_open_positions < 1:
        errors.append("max_open_positions must be >= 1.")
    if not (0 < c.capital_usage_pct <= 100):
        errors.append("capital_usage_pct must be between 0 and 100.")
    if not (0 < c.max_position_pct <= 100):
        errors.append("max_position_pct must be between 0 and 100.")
    if not (0 < c.max_total_exposure_pct <= 100):
        errors.append("max_total_exposure_pct must be between 0 and 100.")
    if c.stop_loss_pct <= 0:
        errors.append("stop_loss_pct must be > 0.")
    if c.max_drawdown_percent <= 0:
        errors.append("max_drawdown_percent must be > 0.")
    if c.fixed_position_toman <= 0:
        errors.append("fixed_position_toman must be > 0.")
    if c.display_position_size_toman <= 0:
        errors.append("display_position_size_toman must be > 0.")
    if c.quantity_step <= 0:
        errors.append("quantity_step must be > 0.")
    if c.fee_buffer_pct < 0 or c.fee_buffer_pct >= 10:
        errors.append("fee_buffer_pct must be between 0 and 10.")
    if c.max_notional_quote <= 0:
        errors.append("max_notional_quote must be > 0.")
    if c.min_notional_quote <= 0:
        errors.append("min_notional_quote must be > 0.")
    if c.min_notional_quote > c.fixed_position_quote and c.position_size_mode == "fixed":
        errors.append("min_notional_quote must not exceed fixed_position_quote.")
    if c.eagle_min_observed_move_pct <= 0:
        errors.append("eagle_min_observed_move_pct must be > 0.")
    if c.eagle_min_1h_pct < 0:
        errors.append("eagle_min_1h_pct must be >= 0.")
    if c.eagle_min_volume_irt < 0:
        errors.append("eagle_min_volume_irt must be >= 0.")
    if c.eagle_max_spread_pct <= 0:
        errors.append("eagle_max_spread_pct must be > 0.")
    return errors