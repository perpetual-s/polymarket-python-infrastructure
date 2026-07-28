"""Category-v2 taker-fee arithmetic.

Pins the curve ``tokens × rate × (p(1−p))^exponent`` (reference:
py-clob-client-v2 fees.py). The per-market (rate, exponent) come from the
feeSchedule at runtime; these tests pin the formula, not category constants.
"""

from decimal import Decimal

from polymarket.models import Side
from polymarket.utils.fees import (
    calculate_net_cost,
    calculate_order_fee,
)


def test_zero_bps_is_fee_free():
    assert calculate_order_fee(Side.BUY, Decimal("0.5"), Decimal("100"), 0) == Decimal(
        "0.0"
    )


def test_formula_arithmetic_at_midpoint():
    # 312 bps, exponent 1, 50¢: tokens=200, curve=0.25 → fee = 200×0.0312×0.25
    fee = calculate_order_fee(Side.BUY, Decimal("0.5"), Decimal("100"), 312)
    assert fee == Decimal("1.56000")


def test_fee_is_symmetric_across_sides():
    buy = calculate_order_fee(Side.BUY, Decimal("0.4"), Decimal("50"), 200)
    sell = calculate_order_fee(Side.SELL, Decimal("0.4"), Decimal("50"), 200)
    assert buy == sell > 0


def test_curve_shrinks_at_extreme_prices():
    mid = calculate_order_fee(Side.BUY, Decimal("0.5"), Decimal("100"), 312)
    edge = calculate_order_fee(Side.BUY, Decimal("0.95"), Decimal("100"), 312)
    assert edge < mid


def test_net_cost_adds_fee_on_buy_and_subtracts_on_sell():
    buy_net, buy_fee = calculate_net_cost(Side.BUY, Decimal("0.5"), Decimal("100"), 312)
    sell_net, sell_fee = calculate_net_cost(
        Side.SELL, Decimal("0.5"), Decimal("100"), 312
    )
    assert buy_fee == sell_fee == Decimal("1.56000")
    assert buy_net == Decimal("101.560000")
    assert sell_net == Decimal("98.440000")


def test_legacy_zero_fee_callers_unchanged():
    net, fee = calculate_net_cost(Side.BUY, Decimal("0.6"), Decimal("100.0"))
    assert (net, fee) == (Decimal("100.000000"), Decimal("0.0"))


def test_fee_rounds_to_current_five_decimal_contract():
    fee = calculate_order_fee(
        Side.BUY,
        Decimal("0.5"),
        Decimal("0.0018"),
        100,
    )
    assert fee == Decimal("0.00000")


def test_fee_exponent_changes_curve_economics():
    exponent_one = calculate_order_fee(
        Side.BUY, Decimal("0.5"), Decimal("100"), 500, Decimal("1")
    )
    exponent_two = calculate_order_fee(
        Side.BUY, Decimal("0.5"), Decimal("100"), 500, Decimal("2")
    )
    assert exponent_two < exponent_one
