"""Pure protective-level calculations used by live and paper execution."""
from __future__ import annotations

from decimal import Decimal


def stop_price(entry_price, stop_loss_pct) -> Decimal:
    return Decimal(str(entry_price)) * (Decimal("1") - Decimal(str(stop_loss_pct)) / 100)


def take_profit_price(entry_price, take_profit_pct) -> Decimal:
    return Decimal(str(entry_price)) * (Decimal("1") + Decimal(str(take_profit_pct)) / 100)


def trailing_price(highest_price, trailing_distance_pct) -> Decimal:
    return Decimal(str(highest_price)) * (Decimal("1") - Decimal(str(trailing_distance_pct)) / 100)
