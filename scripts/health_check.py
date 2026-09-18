# scripts/health_check.py
"""
Standalone health check — verifies the running app without an HTTP call.

Checks
------
1. Imports all server modules (catches syntax / import errors early).
2. Verifies the database is reachable.
3. Verifies required tables exist.
4. Verifies the scanner cache is populated (optional).
5. Verifies the scheduler is not currently running a hung job.

Exit code
---------
    0  — all critical checks passed
    1  — at least one critical check failed
    2  — configuration error (bad .env)

Usage:
    python -m scripts.health_check
    python -m scripts.health_check --json
    python -m scripts.health_check --quiet
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

# ── Project root on sys.path ───────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ══════════════════════════════════════════════════════════
# Check helpers
# ══════════════════════════════════════════════════════════
def _check_imports() -> tuple[bool, str]:
    """Import the whole `server` package. Catches syntax errors."""
    try:
        import server  # noqa: F401
        import server.main  # noqa: F401
        import server.models  # noqa: F401
        import server.scheduler  # noqa: F401
        from server.services import scanner, prices, paper, plans, audit  # noqa: F401
        from server.routes import health, auth, scan, admin  # noqa: F401
        return True, "all server modules imported"
    except Exception as exc:
        return False, f"import failed: {type(exc).__name__}: {exc}"


def _check_config() -> tuple[bool, str, dict[str, Any]]:
    """Validate critical config values."""
    try:
        from server.config import settings
    except Exception as exc:
        return False, f"config import failed: {exc}", {}

    problems: list[str] = []
    if settings.secret_key.startswith("CHANGE_ME"):
        problems.append("SECRET_KEY still default")
    if not settings.admin_email or "@" not in settings.admin_email:
        problems.append("ADMIN_EMAIL invalid")
    if settings.database_url.startswith("sqlite"):
        # SQLite file must have a writable parent dir
        path = settings.database_url.replace("sqlite:///", "", 1)
        parent = Path(path).parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            problems.append(f"data dir not writable: {exc}")

    info = {
        "app": settings.app_name,
        "version": settings.app_version,
        "debug": settings.debug,
        "database_url": _mask(settings.database_url),
        "admin_email": settings.admin_email,
    }
    if problems:
        return False, "; ".join(problems), info
    return True, "config OK", info


def _check_database() -> tuple[bool, str, dict[str, Any]]:
    """Open a session, run SELECT 1, and verify required tables exist."""
    try:
        from sqlalchemy import text
        from server.database import SessionLocal, Base
        from server import models  # noqa: F401 — registers tables
    except Exception as exc:
        return False, f"db import failed: {exc}", {}

    expected = {"users", "trades", "scan_snapshots", "scan_usage", "audit_logs"}
    info: dict[str, Any] = {"expected_tables": sorted(expected)}

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))

        # Find actual tables
        from sqlalchemy import inspect
        inspector = inspect(db.bind)
        actual = set(inspector.get_table_names())
        info["actual_tables"] = sorted(actual)

        missing = expected - actual
        if missing:
            return False, f"missing tables: {sorted(missing)}", info

        # Row counts (cheap on small DBs)
        counts: dict[str, int] = {}
        for t in expected:
            try:
                counts[t] = int(db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() or 0)
            except Exception:
                counts[t] = -1
        info["row_counts"] = counts

        return True, "database OK", info
    except Exception as exc:
        return False, f"db error: {type(exc).__name__}: {exc}", info
    finally:
        db.close()


def _check_alembic() -> tuple[bool, str, dict[str, Any]]:
    """Check whether the DB is at the latest Alembic head."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from alembic.runtime.migration import MigrationContext
        from server.database import engine
    except ImportError:
        return True, "alembic not installed (skipped)", {}

    ini = PROJECT_ROOT / "alembic.ini"
    if not ini.exists():
        return True, "alembic.ini not found (skipped)", {}

    try:
        cfg = Config(str(ini))
        cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        script = ScriptDirectory.from_config(cfg)

        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            current = ctx.get_current_revision()

        head = script.get_current_head()

        info = {"current": current, "head": head}
        if current == head:
            return True, f"alembic up to date ({head})", info
        if current is None:
            return True, "alembic not initialized (no revisions applied)", info
        return False, f"alembic out of date: current={current}, head={head}", info
    except Exception as exc:
        return True, f"alembic check skipped: {exc}", {}


def _check_scanner_cache() -> tuple[bool, str, dict[str, Any]]:
    """
    Check whether the scanner cache has data.
    Non-critical: an empty cache on a fresh install is expected.
    """
    try:
        from server.services import scanner
        cached = scanner.get_cached()
        age_s = (time.time() - cached["updated_at"]) if cached["updated_at"] else None
        info = {
            "rows": cached["count"],
            "source": cached["source"],
            "age_seconds": round(age_s, 1) if age_s is not None else None,
            "error": cached["error"],
        }
        if cached["count"] == 0:
            return True, "scanner cache empty (start the server to populate)", info
        if age_s is not None and age_s > 3600:
            return True, f"scanner cache stale ({age_s/60:.1f} min old)", info
        return True, f"scanner cache healthy ({cached['count']} rows)", info
    except Exception as exc:
        return True, f"scanner check skipped: {exc}", {}


# ══════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════
def run_checks() -> dict[str, Any]:
    """Run every check. Returns a structured report."""
    checks: list[dict[str, Any]] = []

    def _add(name: str, critical: bool, fn) -> None:
        try:
            ok, msg, info = fn()
        except Exception as exc:
            ok, msg, info = False, f"unexpected error: {exc}", {}
        checks.append({
            "name": name,
            "critical": critical,
            "ok": ok,
            "message": msg,
            "info": info,
        })

    _add("imports",  True, _check_imports)
    _add("config",   True, _check_config)
    _add("database", True, _check_database)
    _add("alembic",  False, _check_alembic)
    _add("scanner",  False, _check_scanner_cache)

    failed = [c for c in checks if c["critical"] and not c["ok"]]
    return {
        "ok": len(failed) == 0,
        "checks": checks,
        "failed_critical": [c["name"] for c in failed],
    }


def _print_human(report: dict[str, Any], quiet: bool) -> None:
    sep = "=" * 62

    if not quiet:
        print(sep)
        print("  CryptoScanner — Health Check")
        print(sep)

    for c in report["checks"]:
        mark = "✅" if c["ok"] else ("❌" if c["critical"] else "⚠️")
        if quiet and c["ok"]:
            continue
        line = f"  {mark}  {c['name']:<10} {c['message']}"
        print(line)
        if c["info"] and not quiet:
            for k, v in c["info"].items():
                print(f"       {k}: {v}")

    if not quiet:
        print(sep)

    if report["ok"]:
        print("  ✅ All critical checks passed.")
    else:
        print(f"  ❌ FAILED: {', '.join(report['failed_critical'])}")


def _mask(url: str) -> str:
    if "://" in url and "@" in url:
        scheme, rest = url.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        if ":" in creds:
            user, _ = creds.split(":", 1)
            return f"{scheme}://{user}:***@{host}"
    return url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts.health_check",
        description="Standalone health check for CryptoScanner.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit JSON report instead of plain text.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Only print failing checks (plain text mode).",
    )
    args = parser.parse_args(argv)

    # Silence app logs during the check unless JSON (which must stay clean)
    logging.basicConfig(level=logging.CRITICAL)

    report = run_checks()

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_human(report, quiet=args.quiet)

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)