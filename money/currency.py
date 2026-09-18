"""Centralized money-unit handling for CryptoScanner.

The exchange may speak Rial/RLS while the user configures trade sizes in Toman.
This module keeps that conversion in one place so UI, risk and execution do
not each implement their own 10x conversion.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, InvalidOperation
from typing import Union

Number = Union[int, float, str, Decimal]

RIALS_PER_TOMAN = Decimal("10")


def normalize_currency(value: str | None) -> str:
    key = str(value or "").strip().upper()
    aliases = {"IRR": "RIAL", "RLS": "RIAL", "IRT": "RIAL", "TMN": "TOMAN", "IR": "RIAL"}
    if key in aliases:
        return aliases[key]
    if key in {"RIAL", "TOMAN", "USD", "USDT", "USDC", "BTC", "ETH"}:
        return key
    return key or "RIAL"


def to_decimal(value: Number) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid monetary value: {value!r}") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"Monetary value must be finite and non-negative: {value!r}")
    return result


def toman_to_rial(value: Number) -> Decimal:
    return to_decimal(value) * RIALS_PER_TOMAN


def rial_to_toman(value: Number) -> Decimal:
    return to_decimal(value) / RIALS_PER_TOMAN


def convert(value: Number, from_unit: str, to_unit: str) -> Decimal:
    src = normalize_currency(from_unit)
    dst = normalize_currency(to_unit)
    amount = to_decimal(value)
    if src == dst or src not in {"RIAL", "TOMAN"} or dst not in {"RIAL", "TOMAN"}:
        if src != dst:
            raise ValueError(f"Unsupported conversion: {from_unit} -> {to_unit}")
        return amount
    if src == "TOMAN" and dst == "RIAL":
        return toman_to_rial(amount)
    if src == "RIAL" and dst == "TOMAN":
        return rial_to_toman(amount)
    return amount


def quantize_down(value: Number, step: Number) -> Decimal:
    amount = to_decimal(value)
    increment = to_decimal(step)
    if increment <= 0:
        raise ValueError("step must be > 0")
    units = (amount / increment).to_integral_value(rounding=ROUND_DOWN)
    return units * increment
