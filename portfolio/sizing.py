"""Deterministic, exchange-agnostic position sizing.

Sizing is expressed in the user's display unit (normally Toman for Nobitex)
and converted to the exchange quote unit only at the execution boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from money.currency import convert, normalize_currency, to_decimal


@dataclass(frozen=True)
class SizingLimits:
    fixed_notional: Decimal
    min_notional: Decimal
    max_notional: Decimal
    max_position_pct: Decimal
    max_total_exposure_pct: Decimal


@dataclass(frozen=True)
class PositionSize:
    requested_display: Decimal
    approved_display: Decimal
    approved_quote: Decimal
    quantity: Decimal
    price: Decimal
    quote_unit: str
    display_unit: str
    capped_by_balance: bool = False
    capped_by_position: bool = False
    capped_by_exposure: bool = False


def _d(value) -> Decimal:
    return to_decimal(value)


def calculate_position_size(
    *,
    price,
    account_balance_quote,
    requested_notional_display,
    display_unit: str,
    quote_unit: str,
    max_position_pct=20,
    max_total_exposure_pct=60,
    current_total_exposure_quote=0,
    quantity_step=0.00000001,
    min_quantity=0,
    min_notional_quote=0,
    max_notional_quote=0,
    fee_buffer_pct=0.25,
) -> PositionSize:
    p = _d(price)
    balance = _d(account_balance_quote)
    requested = _d(requested_notional_display)
    exposure = _d(current_total_exposure_quote)
    if p <= 0:
        raise ValueError("price must be > 0")
    if balance <= 0:
        raise ValueError("account balance must be > 0")
    if requested <= 0:
        raise ValueError("requested notional must be > 0")

    display = normalize_currency(display_unit)
    quote = normalize_currency(quote_unit)
    requested_quote = convert(requested, display, quote)

    max_by_balance = balance * (Decimal("1") - _d(fee_buffer_pct) / Decimal("100"))
    max_by_position = balance * _d(max_position_pct) / Decimal("100")
    max_by_exposure = balance * _d(max_total_exposure_pct) / Decimal("100") - exposure

    approved_quote = min(requested_quote, max_by_balance, max_by_position, max_by_exposure)
    capped_balance = approved_quote < requested_quote and approved_quote == max_by_balance
    capped_position = approved_quote < requested_quote and approved_quote == max_by_position
    capped_exposure = approved_quote < requested_quote and approved_quote == max_by_exposure

    if max_notional_quote and approved_quote > _d(max_notional_quote):
        approved_quote = _d(max_notional_quote)
        capped_exposure = True

    min_quote = _d(min_notional_quote)
    if min_quote > 0 and approved_quote < min_quote:
        raise ValueError(
            f"Approved notional {approved_quote} {quote} is below minimum {min_quote} {quote}."
        )

    step = _d(quantity_step)
    quantity = (approved_quote / p / step).to_integral_value(rounding=ROUND_DOWN) * step
    if quantity <= 0 or (min_quantity and quantity < _d(min_quantity)):
        raise ValueError("Calculated quantity is below exchange minimum quantity.")

    approved_quote = quantity * p
    approved_display = convert(approved_quote, quote, display)
    return PositionSize(
        requested_display=requested,
        approved_display=approved_display,
        approved_quote=approved_quote,
        quantity=quantity,
        price=p,
        quote_unit=quote,
        display_unit=display,
        capped_by_balance=capped_balance,
        capped_by_position=capped_position,
        capped_by_exposure=capped_exposure,
    )
