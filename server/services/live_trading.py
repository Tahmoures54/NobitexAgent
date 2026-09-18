# server/services/live_trading.py
"""Guarded live-trading bridge for the production web application.

Live execution is disabled unless LIVE_TRADING_ENABLED=true is configured.
Every live order additionally requires an explicit confirmation flag.
Paper trading never reaches this module.
"""
from __future__ import annotations

from typing import Any

from server.config import settings
from trading.bot_config import BotConfig
from trading.trader import TradingBot


def _bot_config() -> BotConfig:
    return BotConfig(
        exchange="nobitex",
        api_key=settings.nobitex_api_key,
        api_secret=settings.nobitex_private_key,
        testnet=settings.live_trading_testnet,
        spot_mode=True,
        nobitex_market="IRT",
        quote_currency="IRT",
        quote_unit="rial",
        display_currency="TOMAN",
        fixed_position_toman=1_000_000.0,
        fixed_position_quote=10_000_000.0,
        position_size_mode="fixed",
        capital_usage_pct=90.0,
        max_position_pct=90.0,
        max_total_exposure_pct=90.0,
        stop_loss_pct=3.0,
        trailing_stop_enabled=True,
        trailing_activation_pct=3.0,
        trailing_distance_pct=3.0,
        take_profit_percent=50.0,
        max_open_positions=1,
        max_new_entries_per_cycle=1,
        execution_mode="live",
        enable_auto_trading=True,
    )


def get_live_status() -> dict[str, Any]:
    credentials_present = bool(settings.nobitex_api_key and settings.nobitex_private_key)
    return {
        "enabled_by_environment": bool(settings.live_trading_enabled),
        "testnet": bool(settings.live_trading_testnet),
        "credentials_present": credentials_present,
        "explicit_confirmation_required": bool(settings.live_trading_require_explicit_confirmation),
        "default_mode": "paper",
        "live_ready": bool(settings.live_trading_enabled and credentials_present),
    }


def execute_configured_entry(symbol: str, confirmed_live: bool) -> dict[str, Any]:
    if not settings.live_trading_enabled:
        return {"status": "rejected", "reason": "live_trading_disabled", "message": "Live trading is disabled by server configuration."}
    if settings.live_trading_require_explicit_confirmation and not confirmed_live:
        return {"status": "rejected", "reason": "confirmation_required", "message": "Explicit live-trading confirmation is required."}
    if not settings.nobitex_api_key or not settings.nobitex_private_key:
        return {"status": "rejected", "reason": "credentials_missing", "message": "Nobitex live credentials are not configured."}

    bot = TradingBot.get_instance(config=_bot_config(), force=True)
    bot.start()
    result = bot.place_configured_entry(symbol.upper().strip())
    return result
