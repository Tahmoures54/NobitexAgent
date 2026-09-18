# server/config.py
"""Application settings loaded from environment variables and .env."""
from __future__ import annotations
import os
from functools import lru_cache
from typing import List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False)
    app_name: str = "NobitexAgent"
    app_version: str = "1.0.0"
    debug: bool = False
    secret_key: str = Field(default="CHANGE_ME_IN_PRODUCTION_THIS_IS_INSECURE")
    totp_encryption_key: str = Field(default="CHANGE_ME_IN_PRODUCTION_TOTP_ENCRYPTION_KEY")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7
    database_url: str = "sqlite:////app/db/app.db"
    scan_interval_minutes: int = 5
    scan_limit: int = 250
    prices_interval_seconds: int = 60
    paper_initial_cash: float = 1000.0
    paper_capital_usage_pct: float = 90.0
    trailing_activation_pct: float = 3.0
    trailing_distance_pct: float = 3.0
    run_scheduler: bool = True
    auto_create_db: bool = True
    redis_url: str = ""
    admin_email: str = "admin@example.com"
    cors_origins: List[str] = Field(default_factory=list)
    cryptosscanner_cmc_key: str = ""
    web_dir: str = "web"
    market_history_enabled: bool = True
    market_history_retention_days: int = 30
    market_history_max_rows_per_scan: int = 250
    backtest_default_days: int = 7
    backtest_max_days: int = 30
    live_trading_enabled: bool = False
    live_trading_testnet: bool = False
    live_trading_require_explicit_confirmation: bool = True
    nobitex_api_key: str = ""
    nobitex_private_key: str = ""

    @field_validator("secret_key")
    @classmethod
    def _warn_insecure_key(cls, v: str) -> str:
        if v.startswith("CHANGE_ME"):
            import logging
            logging.getLogger(__name__).warning("SECRET_KEY is still the default value. Change it before production.")
        return v

    @field_validator("totp_encryption_key")
    @classmethod
    def _warn_insecure_totp_key(cls, v: str) -> str:
        if v.startswith("CHANGE_ME"):
            import logging
            logging.getLogger(__name__).warning("TOTP_ENCRYPTION_KEY is still the default value. Change it before production.")
        return v

    @field_validator("database_url")
    @classmethod
    def _ensure_data_dir(cls, v: str) -> str:
        if v.startswith("sqlite:///"):
            path = v.replace("sqlite:///", "", 1)
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        return v

    @property
    def is_production(self) -> bool:
        return not self.debug

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
__all__ = ["Settings", "get_settings", "settings"]
