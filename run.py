#!/usr/bin/env python3
# run.py
"""
CryptoScanner Web — entry point.

Usage
-----
Development (auto-reload):
    python run.py

Production:
    python run.py --prod
    # equivalent to:
    #   gunicorn -k uvicorn.workers.UvicornWorker \
    #            -w 1 -b 0.0.0.0:8000 server.main:app

Custom port:
    python run.py --port 9000

Notes
-----
- The MVP runs with a single worker because the scanner cache is
  in-process. Multi-worker deployments must move the cache to
  Redis or Postgres first (see `server/services/scanner.py`).
- Never run `--reload` in production.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# ── Project root on sys.path (so `import server` works) ────
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _ensure_dirs() -> None:
    """Create runtime directories that don't exist yet."""
    for rel in ("data", "data/cache", "data/snapshots", "logs"):
        (PROJECT_ROOT / rel).mkdir(parents=True, exist_ok=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Start the CryptoScanner Web server.",
    )
    parser.add_argument(
        "--host", default=os.getenv("HOST", "127.0.0.1"),
        help="Bind host (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("PORT", "8000")),
        help="Bind port (default: 8000).",
    )
    parser.add_argument(
        "--prod", action="store_true",
        help="Run in production mode (no reload, optimized).",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Number of worker processes (default: 1).",
    )
    parser.add_argument(
        "--log-level", default=None,
        choices=["debug", "info", "warning", "error"],
        help="Uvicorn log level (default: derived from DEBUG env var).",
    )
    parser.add_argument(
        "--version", action="store_true",
        help="Print version and exit.",
    )
    return parser.parse_args(argv)


def _print_banner(host: str, port: int, prod: bool, workers: int) -> None:
    from server.config import settings
    from server.database import Base
    from server import models  # noqa: F401 — ensure models are registered

    mode = "PRODUCTION" if prod else "development"
    print()
    print("=" * 62)
    print(f"  🦅  {settings.app_name} v{settings.app_version}")
    print("=" * 62)
    print(f"  Mode        : {mode}")
    print(f"  Listening   : http://{host}:{port}")
    print(f"  API docs    : http://{host}:{port}/docs")
    print(f"  Frontend    : http://{host}:{port}/")
    print(f"  Workers     : {workers}")
    print(f"  Database    : {_mask(settings.database_url)}")
    print(f"  Admin email : {settings.admin_email}")
    print(f"  Debug       : {settings.debug}")
    print(f"  Tables      : {len(Base.metadata.tables)}")
    print("=" * 62)
    print()


def _mask(url: str) -> str:
    """Hide password in a DB URL."""
    if "://" in url and "@" in url:
        scheme, rest = url.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        if ":" in creds:
            user, _ = creds.split(":", 1)
            return f"{scheme}://{user}:***@{host}"
    return url


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # ── Ensure runtime dirs exist before anything reads them ──
    _ensure_dirs()

    # ── Import settings early to catch .env errors ──
    try:
        from server.config import settings
    except Exception as exc:
        print(f"❌ Failed to load settings: {exc}", file=sys.stderr)
        return 2

    if args.version:
        print(f"{settings.app_name} v{settings.app_version}")
        return 0

    # ── Determine log level ──
    log_level = args.log_level or ("debug" if settings.debug else "info")

    # ── Quick sanity checks before spawning the server ──
    if not args.prod:
        # Warn loudly if SECRET_KEY is still default
        if settings.secret_key.startswith("CHANGE_ME") or settings.secret_key.startswith("dev-only"):
            print(
                "⚠️  WARNING: SECRET_KEY is still a default/dev value. "
                "Never deploy this to production.",
                file=sys.stderr,
            )
    else:
        if settings.debug:
            print(
                "⚠️  WARNING: DEBUG=true in production mode. "
                "Set DEBUG=false in your .env before deploying.",
                file=sys.stderr,
            )
        if settings.secret_key.startswith("CHANGE_ME") or settings.secret_key.startswith("dev-only"):
            print(
                "❌ FATAL: SECRET_KEY is still a default value. "
                "Refusing to start in production.",
                file=sys.stderr,
            )
            return 2

    # ── Banner ──
    _print_banner(args.host, args.port, args.prod, args.workers)

    # ── Run ──
    import uvicorn

    if args.prod:
        # Production: use uvicorn directly (works fine for a single worker)
        uvicorn.run(
            "server.main:app",
            host=args.host,
            port=args.port,
            workers=1,  # MVP: single worker (in-process cache)
            log_level=log_level,
            access_log=True,
            proxy_headers=True,
            forwarded_allow_ips="*",
        )
    else:
        # Development: auto-reload
        uvicorn.run(
            "server.main:app",
            host=args.host,
            port=args.port,
            reload=True,
            reload_dirs=[str(PROJECT_ROOT / "server"), str(PROJECT_ROOT / "web")],
            log_level=log_level,
            access_log=True,
        )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n👋 Shutting down.")
        sys.exit(130)