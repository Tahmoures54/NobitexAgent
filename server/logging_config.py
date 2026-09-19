# server/logging_config.py
"""
Centralized logging configuration.

Three log files under `logs/`:
    - application.log  → everything (INFO+)
    - security.log     → auth/login/admin events (from `server.security`)
    - trading.log      → paper/real trading events (from `server.trading`)

Notes
-----
- On serverless platforms (Vercel, AWS Lambda), the filesystem is
  read-only except for `/tmp`. We auto-detect this and:
    1. Redirect file logs to `/tmp/logs/` when possible.
    2. Fall back to console-only logging if even `/tmp` fails.
- On Vercel, use the Dashboard → Logs tab to view output (stdout).
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional

from server.config import settings

# ── Detect read-only filesystem (Vercel / Lambda) ──────────
_IS_SERVERLESS = bool(
    os.environ.get("VERCEL")
    or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
    or os.environ.get("AWS_EXECUTION_ENV")
)

# ── Constants ──────────────────────────────────────────────
if _IS_SERVERLESS:
    # Only /tmp is writable on Vercel/Lambda
    LOG_DIR = "/tmp/logs"
else:
    LOG_DIR = "logs"

APP_LOG = os.path.join(LOG_DIR, "application.log")
SECURITY_LOG = os.path.join(LOG_DIR, "security.log")
TRADING_LOG = os.path.join(LOG_DIR, "trading.log")

MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
BACKUP_COUNT = 5             # keep 5 rotated files

_FMT = "%(asctime)s | %(levelname)-7s | %(name)-25s | %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def _make_formatter() -> logging.Formatter:
    return logging.Formatter(_FMT, datefmt=_DATE_FMT)


def _make_file_handler(path: str, level: int) -> Optional[RotatingFileHandler]:
    """
    Build a RotatingFileHandler safely.

    Returns None if the filesystem is not writable — the caller
    should then skip adding it. The console handler will still
    emit everything (useful on Vercel).
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setLevel(level)
        handler.setFormatter(_make_formatter())
        return handler
    except OSError as exc:
        # On Vercel the FS is read-only; /tmp usually works, but
        # if it doesn't we silently skip the file handler.
        print(f"[logging_config] file handler disabled ({path}): {exc}", flush=True)
        return None


class _SecurityFilter(logging.Filter):
    """Only let `server.security` and auth-related records through."""
    _PREFIXES = ("server.security", "server.auth", "server.routes.auth", "server.routes.admin")

    def filter(self, record: logging.LogRecord) -> bool:
        return any(record.name.startswith(p) for p in self._PREFIXES)


class _TradingFilter(logging.Filter):
    """Only let trading-related records through."""
    _PREFIXES = ("server.trading", "server.services.paper", "server.services.prices")

    def filter(self, record: logging.LogRecord) -> bool:
        return any(record.name.startswith(p) for p in self._PREFIXES)


def setup_logging(level: Optional[int] = None) -> None:
    """
    Configure root logger and the two specialized loggers.

    Safe to call multiple times — only configures once.
    """
    global _configured
    if _configured:
        return

    if level is None:
        level = logging.DEBUG if settings.debug else logging.INFO

    # ── Root logger ────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    # Console handler (always present — critical on Vercel)
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(_make_formatter())
    root.addHandler(console)

    # Application file handler (optional)
    app_file = _make_file_handler(APP_LOG, level)
    if app_file is not None:
        root.addHandler(app_file)

    # ── Security logger ────────────────────────────────────
    sec_logger = logging.getLogger("server.security")
    sec_logger.setLevel(logging.INFO)
    sec_handler = _make_file_handler(SECURITY_LOG, logging.INFO)
    if sec_handler is not None:
        sec_handler.addFilter(_SecurityFilter())
        sec_logger.addHandler(sec_handler)
    sec_logger.propagate = True  # also goes to app.log + console

    # ── Trading logger ─────────────────────────────────────
    trd_logger = logging.getLogger("server.trading")
    trd_logger.setLevel(logging.INFO)
    trd_handler = _make_file_handler(TRADING_LOG, logging.INFO)
    if trd_handler is not None:
        trd_handler.addFilter(_TradingFilter())
        trd_logger.addHandler(trd_handler)
    trd_logger.propagate = True

    # ── Quiet third-party loggers ──────────────────────────
    for noisy in (
        "urllib3",
        "httpx",
        "httpcore",
        "asyncio",
        "apscheduler",
        "ccxt",
        "PIL",
        "matplotlib",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    logging.getLogger(__name__).info(
        "Logging configured | level=%s | dir=%s | serverless=%s",
        logging.getLevelName(level),
        LOG_DIR,
        _IS_SERVERLESS,
    )


def get_security_logger() -> logging.Logger:
    return logging.getLogger("server.security")


def get_trading_logger() -> logging.Logger:
    return logging.getLogger("server.trading")


__all__ = ["setup_logging", "get_security_logger", "get_trading_logger"]
