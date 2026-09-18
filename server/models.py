# server/models.py
"""
SQLAlchemy ORM models.

Entities
--------
User          — account with plan + subscription status
Trade         — paper trade (open or closed)
ScanSnapshot  — cached scanner result (history / audit)
ScanUsage     — per-user daily scan counter (plan enforcement)
AuditLog      — security-relevant events (login, admin, plan change)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.database import Base


# ── Utilities ──────────────────────────────────────────────
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── User ───────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    plan: Mapped[str] = mapped_column(String(32), default="free", nullable=False)
    plan_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    trades: Mapped[list["Trade"]] = relationship(
        "Trade", back_populates="user", cascade="all, delete-orphan"
    )
    scan_usage: Mapped[list["ScanUsage"]] = relationship(
        "ScanUsage", back_populates="user", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} plan={self.plan}>"


# ── Paper Trade ────────────────────────────────────────────
class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(8), default="long", nullable=False)

    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    entry_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    entry_signal: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    position_size: Mapped[float] = mapped_column(Float, nullable=False)
    notional: Mapped[float] = mapped_column(Float, nullable=False)

    stop_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    take_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    pnl_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)

    user: Mapped[User] = relationship("User", back_populates="trades")

    __table_args__ = (
        Index("ix_trades_user_status", "user_id", "status"),
        Index("ix_trades_symbol_status", "symbol", "status"),
    )

    def __repr__(self) -> str:
        return f"<Trade id={self.id} {self.symbol} {self.side} {self.status}>"


# ── Scan Snapshot ──────────────────────────────────────────
class ScanSnapshot(Base):
    """A point-in-time copy of a scanner result (JSON payload)."""
    __tablename__ = "scan_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string

    def __repr__(self) -> str:
        return f"<ScanSnapshot id={self.id} rows={self.row_count} at={self.created_at}>"


# ── Scan Usage (free plan quota) ───────────────────────────
class ScanUsage(Base):
    """One row per (user, UTC day). Incremented on every manual refresh."""
    __tablename__ = "scan_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    day: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    user: Mapped[User] = relationship("User", back_populates="scan_usage")

    __table_args__ = (
        UniqueConstraint("user_id", "day", name="uq_scan_usage_user_day"),
    )

    def __repr__(self) -> str:
        return f"<ScanUsage user={self.user_id} day={self.day.date()} count={self.count}>"


# ── Audit Log ──────────────────────────────────────────────
class AuditLog(Base):
    """Append-only security & admin event log."""
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    event: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )

    user: Mapped[Optional[User]] = relationship("User", back_populates="audit_logs")

    def __repr__(self) -> str:
        return f"<AuditLog event={self.event!r} user={self.user_id} at={self.created_at}>"


__all__ = ["User", "Trade", "ScanSnapshot", "ScanUsage", "AuditLog"]