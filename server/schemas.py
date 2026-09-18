# server/schemas.py
"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class ProviderTokenReq(BaseModel):
    provider: Literal["google", "microsoft"]
    id_token: str = Field(min_length=20, max_length=20000)


class ProfileUpdateReq(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    phone_number: str = Field(min_length=7, max_length=32)

    @field_validator("full_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        value = value.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if not value.startswith("+") or not value[1:].isdigit():
            raise ValueError("Phone number must use international format, for example +989121234567.")
        return value


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    auth_provider: str
    full_name: Optional[str] = None
    phone_number: Optional[str] = None
    profile_complete: bool = False
    plan: str
    is_active: bool
    created_at: datetime
    last_login_at: Optional[datetime] = None
    plan_expires_at: Optional[datetime] = None

    @classmethod
    def from_user(cls, user: Any) -> "UserOut":
        return cls(
            id=user.id,
            email=user.email,
            auth_provider=user.auth_provider,
            full_name=user.full_name,
            phone_number=user.phone_number,
            profile_complete=bool(user.full_name and user.phone_number),
            plan=user.plan,
            is_active=user.is_active,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
            plan_expires_at=user.plan_expires_at,
        )


class TokenResp(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class AuthConfigOut(BaseModel):
    google_client_id: str
    microsoft_client_id: str
    microsoft_authority: str


class ScanResponse(BaseModel):
    updated_at: float
    count: int
    rows: list[dict[str, Any]]


class ScanRefreshResp(BaseModel):
    ok: bool
    count: int
    remaining_today: Optional[int] = None


class OpenTradeReq(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    side: Literal["long", "short"] = "long"
    price: float = Field(gt=0)
    size_usd: float = Field(gt=0, default=100.0, le=10_000_000)
    stop_loss_pct: float = Field(gt=0, le=50, default=3.0)
    take_profit_pct: float = Field(gt=0, le=200, default=50.0)
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
    highest_price: Optional[float]
    lowest_price: Optional[float]
    trailing_stop: Optional[float]
    trailing_active: bool
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


class AdminStatsOut(BaseModel):
    total_users: int
    premium_users: int
    free_users: int
    total_trades: int
    open_trades: int


class HealthOut(BaseModel):
    status: str
    version: str
    database: str


class ErrorResp(BaseModel):
    detail: str


__all__ = [
    "ProviderTokenReq", "ProfileUpdateReq", "UserOut", "TokenResp", "AuthConfigOut",
    "ScanResponse", "ScanRefreshResp", "OpenTradeReq", "CloseTradeReq", "TradeOut",
    "PaperStatsOut", "AdminStatsOut", "HealthOut", "ErrorResp",
]
