# server/scheduler.py
"""
Background jobs.

Jobs
----
- scan_job     : every SCAN_INTERVAL_MINUTES — refresh the scanner cache
- prices_job   : every PRICES_INTERVAL_SECONDS — update prices and
                 auto-close paper trades that hit SL/TP

Both jobs are wrapped so that one failure never kills the scheduler.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from server.config import settings
from server.database import SessionLocal

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None
_lock = threading.Lock()


# ══════════════════════════════════════════════════════════
# Job: scan
# ══════════════════════════════════════════════════════════
def _scan_job() -> None:
    try:
        from server.services import scanner
        n = scanner.run_scan(persist_snapshot=True)
        logger.debug("Scheduled scan finished | rows=%d", n)
    except Exception as exc:
        logger.exception("Scan job failed: %s", exc)


# ══════════════════════════════════════════════════════════
# Job: prices → auto-close SL/TP for paper trades
# ══════════════════════════════════════════════════════════
def _prices_job() -> None:
    """
    Fetch live prices for symbols that have open paper trades,
    then close any trade whose SL or TP has been touched.
    """
    db = SessionLocal()
    try:
        from server.models import Trade
        from server.services import paper as paper_svc
        from server.services import prices as price_svc

        # All symbols with at least one open trade
        rows = (
            db.query(Trade.symbol)
            .filter(Trade.status == "open")
            .distinct()
            .all()
        )
        symbols = [r[0] for r in rows if r[0]]
        if not symbols:
            return

        price_map = price_svc.get_prices(symbols)
        if not price_map:
            logger.debug("Prices job: no prices available for %s", symbols)
            return

        # All users with at least one open trade
        user_rows = (
            db.query(Trade.user_id)
            .filter(Trade.status == "open")
            .distinct()
            .all()
        )
        user_ids = [r[0] for r in user_rows if r[0] is not None]

        total_closed = 0
        for uid in user_ids:
            try:
                closed = paper_svc.check_exits(db, uid, price_map)
                total_closed += closed
            except Exception as exc:
                logger.exception("check_exits failed for user=%s: %s", uid, exc)

        if total_closed:
            logger.info(
                "Prices job: auto-closed %d trades | symbols=%d | users=%d",
                total_closed, len(symbols), len(user_ids),
            )
    except Exception as exc:
        logger.exception("Prices job failed: %s", exc)
    finally:
        db.close()


# ══════════════════════════════════════════════════════════
# Lifecycle
# ══════════════════════════════════════════════════════════
def start_scheduler() -> None:
    """Start the scheduler. Idempotent."""
    global _scheduler
    with _lock:
        if _scheduler is not None:
            logger.debug("Scheduler already running")
            return

        sched = BackgroundScheduler(
            timezone="UTC",
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 30,
            },
        )

        sched.add_job(
            _scan_job,
            trigger=IntervalTrigger(minutes=settings.scan_interval_minutes),
            id="scan_job",
            name="Scanner refresh",
            replace_existing=True,
        )

        sched.add_job(
            _prices_job,
            trigger=IntervalTrigger(seconds=settings.prices_interval_seconds),
            id="prices_job",
            name="Paper SL/TP watcher",
            replace_existing=True,
        )

        sched.start()
        _scheduler = sched

        logger.info(
            "Scheduler started | scan every %dm | prices every %ds",
            settings.scan_interval_minutes,
            settings.prices_interval_seconds,
        )

    # Kick off the first scan in a daemon thread so startup isn't blocked
    threading.Thread(
        target=_scan_job,
        daemon=True,
        name="initial-scan",
    ).start()


def stop_scheduler(wait: bool = True) -> None:
    """Gracefully stop the scheduler."""
    global _scheduler
    with _lock:
        if _scheduler is None:
            return
        try:
            _scheduler.shutdown(wait=wait)
            logger.info("Scheduler stopped")
        except Exception as exc:
            logger.warning("Scheduler shutdown error: %s", exc)
        finally:
            _scheduler = None


def get_scheduler() -> Optional[BackgroundScheduler]:
    return _scheduler


__all__ = ["start_scheduler", "stop_scheduler", "get_scheduler"]