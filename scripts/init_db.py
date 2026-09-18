# scripts/init_db.py
"""
Initialize the database.

Behaviour
---------
1. Ensures the `data/` directory exists.
2. If Alembic is installed and a `alembic/` directory is present,
   runs `alembic upgrade head` (recommended).
3. Otherwise falls back to SQLAlchemy's `Base.metadata.create_all`.

Idempotent — safe to run on an existing database.

Usage:
    python -m scripts.init_db
    python -m scripts.init_db --force       # drop & recreate (DANGEROUS)
    python -m scripts.init_db --no-alembic  # skip Alembic, use create_all
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# ── Ensure project root is importable ──────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _bootstrap_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _ensure_data_dir() -> Path:
    """Make sure the `data/` directory exists. Returns its path."""
    data_dir = PROJECT_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _alembic_available() -> bool:
    """Alembic is usable if the package is installed and alembic.ini exists."""
    try:
        import alembic  # noqa: F401
    except ImportError:
        return False
    return (PROJECT_ROOT / "alembic.ini").exists()


def _run_alembic_upgrade() -> int:
    """Run `alembic upgrade head` programmatically. Returns exit code."""
    from alembic.config import Config
    from alembic import command

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    return 0


def _run_create_all() -> int:
    """Fallback: create tables directly from SQLAlchemy metadata."""
    from server.database import init_db as _init
    _init()
    return 0


def _drop_all() -> int:
    """Danger: drop every table."""
    from server.database import drop_all
    drop_all()
    print("⚠️  All tables dropped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts.init_db",
        description="Initialize the CryptoScanner database.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Drop all tables before creating (DANGEROUS).",
    )
    parser.add_argument(
        "--no-alembic", action="store_true",
        help="Skip Alembic even if it is available.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show debug logs.",
    )
    args = parser.parse_args(argv)

    _bootstrap_logging(args.verbose)
    log = logging.getLogger("init_db")

    # 1. Data directory
    data_dir = _ensure_data_dir()
    log.info("Data directory ready: %s", data_dir)

    # 2. Config
    from server.config import settings
    log.info("Database URL: %s", _mask_url(settings.database_url))

    # 3. Optional drop
    if args.force:
        log.warning("--force specified: dropping all tables.")
        _drop_all()

    # 4. Upgrade or create
    if not args.no_alembic and _alembic_available():
        log.info("Using Alembic (upgrade head)...")
        try:
            _run_alembic_upgrade()
            log.info("Alembic upgrade complete.")
            return 0
        except Exception as exc:
            log.error("Alembic upgrade failed: %s", exc)
            log.info("Falling back to Base.metadata.create_all ...")

    # 5. Fallback
    log.info("Using SQLAlchemy create_all ...")
    _run_create_all()
    log.info("Tables created successfully.")

    # 6. Report tables
    from server.database import Base
    tables = sorted(Base.metadata.tables.keys())
    log.info("Known tables (%d): %s", len(tables), ", ".join(tables))
    return 0


def _mask_url(url: str) -> str:
    """Hide password in a DB URL."""
    if "://" in url and "@" in url:
        scheme, rest = url.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        if ":" in creds:
            user, _ = creds.split(":", 1)
            return f"{scheme}://{user}:***@{host}"
    return url


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)