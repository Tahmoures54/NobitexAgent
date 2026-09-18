# server/config.py
"""
Application settings loaded from environment variables and `.env`.

All values have safe defaults so the app can boot in development
without any configuration. In production, override via `.env`.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── App ────────────────────────────────────────────────
    app_name: str = "CryptoScanner Web"
    app_version: str = "0.1.0"
    debug: bool = False

    # ── Security ───────────────────────────────────────────
    secret_key: str = Field(
        default="CHANGE_ME_IN_PRODUCTION_THIS_IS_INSECURE",
        description="JWT signing key. MUST be changed in production.",
    )
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days

    # ── Database ───────────────────────────────────────────
    # SQLite for MVP. Switch to Postgres by changing this URL only.
    database_url: str = "sqlite:///./data/app.db"

    # ── Scanner ────────────────────────────────────────────
    scan_interval_minutes: int = 5
    scan_limit: int = 250

    # ── Prices watcher (Paper SL/TP auto-close) ────────────
    prices_interval_seconds: int = 60
    # Run APScheduler inside this process. Set false for the public web service
    # when a dedicated worker service is used.
    run_scheduler: bool = True
    # Automatically create missing tables at startup. Production should use Alembic.
    auto_create_db: bool = True
    # Optional shared rate-limit store (for multi-instance deployments).
    redis_url: str = ""

    # ── Admin ──────────────────────────────────────────────
    admin_email: str = "admin@example.com"

    # ── CORS ───────────────────────────────────────────────
    # Empty list = CORS disabled (frontend served by same FastAPI).
    cors_origins: List[str] = Field(default_factory=list)

    # ── Optional external APIs ─────────────────────────────
    cryptosscanner_cmc_key: str = ""

    # ── Web directory (auto-detected) ──────────────────────
    web_dir: str = "web"

    @field_validator("secret_key")
    @classmethod
    def _warn_insecure_key(cls, v: str) -> str:
        if v.startswith("CHANGE_ME"):
            import logging
            logging.getLogger(__name__).warning(
                "SECRET_KEY is still the default value. "
                "Change it before deploying to production."
            )
        return v

    @field_validator("database_url")
    @classmethod
    def _ensure_data_dir(cls, v: str) -> str:
        """If SQLite, ensure the parent directory exists."""
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
    """Cached settings singleton."""
    return Settings()


# Module-level convenience alias used across the codebase.
settings = get_settings()


__all__ = ["Settings", "get_settings", "settings"]