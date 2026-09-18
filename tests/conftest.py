# tests/conftest.py
"""
Shared pytest fixtures.

Key design decisions
--------------------
1. Every test gets a **fresh in-memory SQLite database** with WAL off
   and foreign keys on. This isolates tests completely.
2. The FastAPI `app` object is imported once, but its `get_db`
   dependency is overridden per-test to use the isolated session.
3. The background scheduler is *never* started in tests — we invoke
   jobs manually where needed.
4. `httpx`-based `TestClient` is used synchronously for simplicity.
"""
from __future__ import annotations

import os
import sys
import tempfile
from typing import Generator

import pytest

# ── Ensure project root is importable ──────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── Set safe env vars BEFORE importing the app ─────────────
os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.local")
os.environ.setdefault("SCAN_INTERVAL_MINUTES", "60")  # quiet in tests

# ── Now safe to import the app modules ────────────────────
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from server.database import Base, get_db  # noqa: E402
from server import models  # noqa: F401,E402 — register models
from server.main import app  # noqa: E402


# ══════════════════════════════════════════════════════════
# Database
# ══════════════════════════════════════════════════════════
@pytest.fixture(scope="function")
def db_engine():
    """
    Fresh in-memory SQLite engine per test.

    Uses StaticPool so all connections share the same in-memory DB.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    # Enable FK enforcement (SQLite defaults to off)
    @event.listens_for(engine, "connect")
    def _fk_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine) -> Generator[Session, None, None]:
    """A SQLAlchemy session bound to the isolated engine."""
    SessionLocal = sessionmaker(
        bind=db_engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ══════════════════════════════════════════════════════════
# FastAPI TestClient (with overridden DB)
# ══════════════════════════════════════════════════════════
@pytest.fixture(scope="function")
def client(db_session) -> Generator[TestClient, None, None]:
    """
    TestClient with `get_db` overridden to use the test session.

    Scheduler is not started because we never call the lifespan
    startup — TestClient only runs lifespan if used as a context
    manager. We deliberately skip that.
    """
    def _override_get_db() -> Generator[Session, None, None]:
        try:
            yield db_session
        finally:
            # Do not close the session here — pytest fixture owns it.
            pass

    app.dependency_overrides[get_db] = _override_get_db

    # Instantiate TestClient WITHOUT triggering lifespan
    c = TestClient(app, raise_server_exceptions=True)

    try:
        yield c
    finally:
        app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════
# User fixtures
# ══════════════════════════════════════════════════════════
@pytest.fixture
def user_credentials() -> dict[str, str]:
    return {
        "email": "alice@example.com",
        "full_name": "Alice Example",
        "phone_number": "+989121234567",
    }


def _register_and_activate(client, payload):
    import pyotp
    resp = client.post("/auth/register", json=payload)
    assert resp.status_code == 200, resp.text
    setup = resp.json()
    code = pyotp.TOTP(setup["secret"]).now()
    verify = client.post(
        "/auth/setup/verify",
        json={"email": payload["email"], "code": code},
    )
    assert verify.status_code == 200, verify.text
    return verify.json()


@pytest.fixture
def free_user(client, user_credentials) -> dict:
    data = _register_and_activate(client, user_credentials)
    return {
        "token": data["access_token"],
        "user": data["user"],
        "email": user_credentials["email"],
    }


@pytest.fixture
def auth_headers(free_user) -> dict[str, str]:
    return {"Authorization": f"Bearer {free_user['token']}"}


@pytest.fixture
def pro_user(client, db_session) -> dict:
    payload = {
        "email": "pro@example.com",
        "full_name": "Pro User",
        "phone_number": "+989121234568",
    }
    data = _register_and_activate(client, payload)
    from server.models import User
    user = db_session.get(User, data["user"]["id"])
    assert user is not None
    user.plan = "pro"
    user.plan_expires_at = None
    db_session.commit()
    return {"token": data["access_token"], "user": data["user"], "email": payload["email"]}


@pytest.fixture
def pro_headers(pro_user) -> dict[str, str]:
    return {"Authorization": f"Bearer {pro_user['token']}"}


@pytest.fixture
def admin_user(client, db_session, monkeypatch) -> dict:
    payload = {
        "email": "admin@test.local",
        "full_name": "Admin User",
        "phone_number": "+989121234569",
    }
    data = _register_and_activate(client, payload)
    return {"token": data["access_token"], "user": data["user"], "email": payload["email"]}


@pytest.fixture
def admin_headers(admin_user) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_user['token']}"}


# ══════════════════════════════════════════════════════════
# Scanner stub
# ══════════════════════════════════════════════════════════
@pytest.fixture
def fake_scan_rows() -> list[dict]:
    """Deterministic scan rows for tests."""
    return [
        {
            "Symbol": "BTC",
            "Name": "Bitcoin",
            "Price": 50000.0,
            "1h Change (%)": 0.5,
            "24h Change (%)": 2.1,
            "7d Change (%)": 5.3,
            "RSI": 55.0,
            "Volume": 1.2e9,
            "Market Cap": 9.8e11,
            "Signal": "Buy Signal",
            "Score": 32.5,
            "Risk": "Low",
        },
        {
            "Symbol": "ETH",
            "Name": "Ethereum",
            "Price": 3000.0,
            "1h Change (%)": -0.2,
            "24h Change (%)": 1.4,
            "7d Change (%)": 3.0,
            "RSI": 48.0,
            "Volume": 6.0e8,
            "Market Cap": 3.6e11,
            "Signal": "Neutral",
            "Score": 12.0,
            "Risk": "Low",
        },
        {
            "Symbol": "DOGE",
            "Name": "Dogecoin",
            "Price": 0.15,
            "1h Change (%)": 3.8,
            "24h Change (%)": 8.2,
            "7d Change (%)": 15.0,
            "RSI": 72.0,
            "Volume": 4.5e8,
            "Market Cap": 2.1e10,
            "Signal": "Strong Buy",
            "Score": 68.0,
            "Risk": "Medium",
        },
    ]


@pytest.fixture
def stub_scanner(monkeypatch, fake_scan_rows):
    """
    Replace `scanner.run_scan` so tests don't hit the network.

    Sets `updated_at` to now so freshness checks pass.
    """
    import time
    from server.services import scanner

    def _fake_run_scan(*args, **kwargs) -> int:
        with scanner._LOCK:
            scanner._CACHE["rows"] = list(fake_scan_rows)
            scanner._CACHE["updated_at"] = time.time()
            scanner._CACHE["error"] = None
            scanner._CACHE["source"] = "stub"
        return len(fake_scan_rows)

    monkeypatch.setattr(scanner, "run_scan", _fake_run_scan)
    # Pre-warm cache so `/api/scan` returns rows without calling refresh
    _fake_run_scan()
    return scanner


# ══════════════════════════════════════════════════════════
# Pytest configuration
# ══════════════════════════════════════════════════════════
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "slow: mark test as slow")
    config.addinivalue_line("markers", "integration: mark as integration test")