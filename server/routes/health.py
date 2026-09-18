# server/routes/health.py
"""
Health & readiness endpoints.

    GET /health      — liveness (always 200 if the process is up)
    GET /health/db   — readiness (checks DB connectivity)
    GET /health/full — detailed diagnostics (uptime, scheduler, cache)
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, status, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from server.config import settings
from server.database import get_db
from server.schemas import HealthOut

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

_STARTED_AT = time.time()


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    """Liveness check. Returns 200 whenever the app is up."""
    return HealthOut(status="ok", version=settings.app_version, database="unchecked")


@router.get("/health/db")
def health_db(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Readiness check — verifies DB connectivity with a trivial query."""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok"}
    except Exception as exc:
        logger.warning("Health DB check failed: %s", exc)
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "database": "unreachable", "error": "database unavailable"},
        )


@router.get("/health/full")
def health_full(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Detailed diagnostics — safe to expose, contains no secrets."""
    from server.scheduler import get_scheduler
    from server.services import scanner

    db_ok = True
    db_err = None
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        db_ok = False
        db_err = str(exc)

    sched = get_scheduler()
    jobs = []
    if sched is not None:
        for job in sched.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            })

    cache = scanner.get_cached()

    return {
        "status": "ok" if db_ok else "degraded",
        "app": settings.app_name,
        "version": settings.app_version,
        "debug": settings.debug,
        "uptime_seconds": round(time.time() - _STARTED_AT, 1),
        "database": {"ok": db_ok, "error": db_err},
        "scheduler": {"running": sched is not None, "jobs": jobs},
        "scanner": {
            "rows": cache["count"],
            "updated_at": cache["updated_at"],
            "source": cache["source"],
            "error": cache["error"],
        },
    }


__all__ = ["router"]