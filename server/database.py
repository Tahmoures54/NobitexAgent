# server/database.py
"""
SQLAlchemy engine, session factory, and Base.

Notes
-----
- Uses SQLite with WAL mode by default for dev (fast + safe).
- Switching to Postgres requires only changing `DATABASE_URL`.
- `get_db` is a FastAPI dependency that yields one session per request.
"""
from __future__ import annotations

import logging
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from server.config import settings

logger = logging.getLogger(__name__)


# ── Engine ─────────────────────────────────────────────────
_is_sqlite = settings.database_url.startswith("sqlite")

_connect_args = {"check_same_thread": False} if _is_sqlite else {}

engine: Engine = create_engine(
    settings.database_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
    connect_args=_connect_args,
)


# ── SQLite tuning (WAL + foreign keys) ─────────────────────
@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _connection_record) -> None:
    """Enable WAL and foreign keys on every SQLite connection."""
    if not _is_sqlite:
        return
    try:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()
    except Exception as exc:
        logger.warning("Could not apply SQLite pragmas: %s", exc)


# ── Session factory ────────────────────────────────────────
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


# ── Base class ─────────────────────────────────────────────
class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


# ── FastAPI dependency ─────────────────────────────────────
def get_db() -> Generator[Session, None, None]:
    """
    Yield a database session for the duration of one request.

    Guarantees the session is closed even if the request fails.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Init helpers ───────────────────────────────────────────
def init_db() -> None:
    """Create all tables. Safe to call on every startup."""
    from server import models  # noqa: F401 — register models

    Base.metadata.create_all(bind=engine)
    logger.info(
        "Database initialized | url=%s | tables=%d",
        _mask_url(settings.database_url),
        len(Base.metadata.tables),
    )


def drop_all() -> None:
    """Drop every table. Only for tests."""
    from server import models  # noqa: F401

    Base.metadata.drop_all(bind=engine)


def _mask_url(url: str) -> str:
    """Hide password in database URLs when logging."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            creds, host = rest.rsplit("@", 1)
            if ":" in creds:
                user, _ = creds.split(":", 1)
                return f"{scheme}://{user}:***@{host}"
    return url


__all__ = ["Base", "engine", "SessionLocal", "get_db", "init_db", "drop_all"]