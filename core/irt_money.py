"""Explicit IRT/Rial/Toman conversion helpers.

Nobitex uses IRT/RLS balances as Iranian Rial. The UI may display Toman,
where 1 Toman = 10 Rial. Keep exchange/API amounts in Rial and convert only
at presentation or user-input boundaries.
"""
from __future__ import annotations

RIALS_PER_TOMAN = 10


def is_irt_quote(quote: str) -> bool:
    return str(quote or "").upper() in {"IRT", "RLS", "IRR"}


def display_quote_label(quote: str) -> str:
    if is_irt_quote(quote):
        return "IRT (Rial)"
    text = str(quote or "USDT").strip().upper()
    return text or "USDT"


def rial_to_toman(amount: float) -> float:
    return float(amount) / RIALS_PER_TOMAN


def toman_to_rial(amount: float) -> float:
    return float(amount) * RIALS_PER_TOMAN


def parse_amount(text: str, default: float = 0.0) -> float:
    try:
        return float(str(text).replace(",", "").strip() or default)
    except (TypeError, ValueError):
        return float(default)


def parse_toman(text: str, default: float = 0.0) -> float:
    """Parse a user-facing Toman amount and return Rial for API/order use."""
    return toman_to_rial(parse_amount(text, default))


def format_toman(amount_rial: float, decimals: int = 0) -> str:
    """Format a Rial amount as Toman for display."""
    return f"{rial_to_toman(amount_rial):,.{max(0, int(decimals))}f}"


__all__ = [
    "RIALS_PER_TOMAN",
    "is_irt_quote",
    "display_quote_label",
    "rial_to_toman",
    "toman_to_rial",
    "parse_amount",
    "parse_toman",
    "format_toman",
]
