"""Dedicated scheduler process for production deployments.

The public FastAPI process should run with RUN_SCHEDULER=false.
This worker owns scanner refreshes and paper SL/TP checks.
"""
from __future__ import annotations

import logging
import signal
import time

from server.config import settings
from server.logging_config import setup_logging
from server.scheduler import start_scheduler, stop_scheduler

setup_logging()
logger = logging.getLogger(__name__)

_running = True


def _stop(_signum, _frame) -> None:
    global _running
    _running = False


def main() -> None:
    if not settings.run_scheduler:
        raise RuntimeError("Worker requires RUN_SCHEDULER=true")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    logger.info("NobitexAgent worker starting")
    start_scheduler()

    try:
        while _running:
            time.sleep(1)
    finally:
        stop_scheduler(wait=True)
        logger.info("NobitexAgent worker stopped")


if __name__ == "__main__":
    main()
