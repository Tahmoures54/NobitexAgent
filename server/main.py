# server/main.py
"""
FastAPI application entry point.

Wires together:
    - settings + logging
    - database init
    - background scheduler
    - global exception handlers
    - rate limiter middleware
    - API routes
    - static frontend (web/)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.exc import IntegrityError

from server.config import settings
from server.database import init_db
from server.logging_config import setup_logging
from server.scheduler import start_scheduler, stop_scheduler
from server.security import limiter


# ══════════════════════════════════════════════════════════
# Logging must be configured before any other import logs
# ══════════════════════════════════════════════════════════
setup_logging()
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# Lifespan
# ══════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("Starting %s v%s", settings.app_name, settings.app_version)
    logger.info("Debug=%s | Database=%s", settings.debug, settings.database_url)
    logger.info("=" * 60)

    # Startup
    if not settings.debug and (
        settings.secret_key.startswith("CHANGE_ME")
        or settings.secret_key.startswith("dev-only")
        or len(settings.secret_key) < 32
    ):
        raise RuntimeError(
            "Production startup refused: SECRET_KEY must be a unique value "
            "with at least 32 characters."
        )

    if not settings.debug and settings.database_url.startswith("sqlite"):
        raise RuntimeError(
            "Production startup refused: DATABASE_URL must use PostgreSQL; "
            "SQLite is development-only for this hosted service."
        )

    if settings.auto_create_db:
        init_db()
    if settings.run_scheduler:
        start_scheduler()

    yield

    # Shutdown
    if settings.run_scheduler:
        stop_scheduler(wait=True)
    logger.info("Shutdown complete")


# ══════════════════════════════════════════════════════════
# App
# ══════════════════════════════════════════════════════════
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Crypto scanner + paper trading SaaS backend.",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


# ══════════════════════════════════════════════════════════
# Rate limiting
# ══════════════════════════════════════════════════════════
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(_request: Request, exc: RateLimitExceeded) -> JSONResponse:
    logger.warning("Rate limit exceeded: %s", exc.detail)
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please slow down."},
    )


# ══════════════════════════════════════════════════════════
# Global exception handlers
# ══════════════════════════════════════════════════════════
@app.exception_handler(IntegrityError)
async def _integrity_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    logger.warning("IntegrityError on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=409,
        content={"detail": "Duplicate or invalid data."},
    )


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )


# ══════════════════════════════════════════════════════════
# Basic security headers
# ══════════════════════════════════════════════════════════
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if not settings.debug:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


# ══════════════════════════════════════════════════════════
# CORS (only if frontend is hosted elsewhere)
# ══════════════════════════════════════════════════════════
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=3600,
    )
    logger.info("CORS enabled for %s", settings.cors_origins)


# ══════════════════════════════════════════════════════════
# Routes
# ══════════════════════════════════════════════════════════
from server.routes import auth as auth_routes  # noqa: E402
from server.routes import scan as scan_routes  # noqa: E402
from server.routes import paper as paper_routes  # noqa: E402
from server.routes import admin as admin_routes  # noqa: E402
from server.routes import health as health_routes  # noqa: E402

app.include_router(health_routes.router)
app.include_router(auth_routes.router)
app.include_router(scan_routes.router)
app.include_router(paper_routes.router)
app.include_router(admin_routes.router)


# ══════════════════════════════════════════════════════════
# Static frontend
# ══════════════════════════════════════════════════════════
WEB_DIR = Path(__file__).resolve().parent.parent / settings.web_dir

if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    logger.info("Frontend served from %s", WEB_DIR)
else:
    logger.warning(
        "Frontend directory '%s' not found — API-only mode. "
        "Create 'web/' or change WEB_DIR.",
        WEB_DIR,
    )


# ══════════════════════════════════════════════════════════
# Direct execution helper
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server.main:app",
        host="127.0.0.1",
        port=8000,
        reload=settings.debug,
    )