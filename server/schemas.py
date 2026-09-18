# server/schemas.py
"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ══════════════════════════════════════════════════════════
# Auth
# ══════════════════════════════════════════════════════════
class RegisterReq(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class LoginReq(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    plan: str
    is_active: bool
    created_at: datetime
    last_login_at: Optional[datetime] = None
    plan_expires_at: Optional[datetime] = None


class TokenResp(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ══════════════════════════════════════════════════════════
# Scanner
# ══════════════════════════════════════════════════════════
class ScanResponse(BaseModel):
    updated_at: float
    count: int
    rows: list[dict[str, Any]]


class ScanRefreshResp(BaseModel):
    ok: bool
    count: int
    remaining_today: Optional[int] = None  # None = unlimited


# ══════════════════════════════════════════════════════════
# Paper Trading
# ══════════════════════════════════════════════════════════
class OpenTradeReq(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    side: Literal["long", "short"] = "long"
    price: float = Field(gt=0)
    size_usd: float = Field(gt=0, default=100.0, le=10_000_000)
    stop_loss_pct: float = Field(gt=0, le=50, default=2.0)
    take_profit_pct: float = Field(gt=0, le=200, default=6.0)
    signal: Optional[str] = Field(default=None, max_length=64)


class CloseTradeReq(BaseModel):
    price: float = Field(gt=0)
    reason: str = Field(default="Manual", max_length=64)


class TradeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    side: str
    entry_price: float
    entry_time: datetime
    exit_price: Optional[float]
    exit_time: Optional[datetime]
    position_size: float
    notional: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    pnl_pct: Optional[float]
    pnl_usd: Optional[float]
    status: str
    exit_reason: Optional[str]
    entry_signal: Optional[str]


class PaperStatsOut(BaseModel):
    total_trades: int
    open_trades: int
    closed_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl_usd: float
    avg_pnl_pct: float


# ══════════════════════════════════════════════════════════
# Admin
# ══════════════════════════════════════════════════════════
class AdminStatsOut(BaseModel):
    total_users: int
    premium_users: int
    free_users: int
    total_trades: int
    open_trades: int


# ══════════════════════════════════════════════════════════
# Generic
# ══════════════════════════════════════════════════════════
class HealthOut(BaseModel):
    status: str
    version: str
    database: str


class ErrorResp(BaseModel):
    detail: str


__all__ = [
    "RegisterReq", "LoginReq", "UserOut", "TokenResp",
    "ScanResponse", "ScanRefreshResp",
    "OpenTradeReq", "CloseTradeReq", "TradeOut", "PaperStatsOut",
    "AdminStatsOut", "HealthOut", "ErrorResp",
]