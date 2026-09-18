# server/routes/live.py
"""Admin-only, explicitly confirmed live-trading endpoints."""
from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends
from server.auth import get_current_user
from server.models import User
from server.security import require_admin
from server.services import live_trading

router = APIRouter(prefix="/api/live", tags=["live"])


class LiveEntryRequest(BaseModel):
    symbol: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_/-]+$")
    confirmed_live: bool = False


@router.get("/status")
def status(_admin: User = Depends(require_admin)) -> dict:
    return live_trading.get_live_status()


@router.post("/entry")
def entry(req: LiveEntryRequest, _admin: User = Depends(require_admin)) -> dict:
    return live_trading.execute_configured_entry(req.symbol, req.confirmed_live)
