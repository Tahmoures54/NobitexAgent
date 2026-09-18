from decimal import Decimal

from money.currency import rial_to_toman, toman_to_rial
from portfolio.sizing import calculate_position_size


def test_toman_rial_conversion():
    assert toman_to_rial(1_000_000) == Decimal("10000000")
    assert rial_to_toman(10_000_000) == Decimal("1000000")


def test_one_million_toman_becomes_ten_million_rial():
    size = calculate_position_size(
        price=100_000,
        account_balance_quote=50_000_000,
        requested_notional_display=1_000_000,
        display_unit="TOMAN",
        quote_unit="RIAL",
        max_position_pct=20,
        max_total_exposure_pct=60,
        quantity_step=0.00000001,
        min_notional_quote=10_000_000,
        max_notional_quote=15_000_000,
        fee_buffer_pct=0.25,
    )
    assert size.approved_quote == Decimal("10000000")
    assert size.approved_display == Decimal("1000000")
    assert size.quantity == Decimal("100")


def test_balance_cap_never_exceeds_position_limit():
    size = calculate_position_size(
        price=100_000,
        account_balance_quote=20_000_000,
        requested_notional_display=1_000_000,
        display_unit="TOMAN",
        quote_unit="RIAL",
        max_position_pct=20,
        max_total_exposure_pct=60,
        quantity_step=0.00000001,
        min_notional_quote=1_000_000,
        max_notional_quote=20_000_000,
        fee_buffer_pct=0,
    )
    assert size.approved_quote == Decimal("4000000")
