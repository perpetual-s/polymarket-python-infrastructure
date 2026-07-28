"""Category-v2 taker-fee arithmetic (core-loop Task B2).

Pins the curve ``tokens × rate × (p(1−p))^exponent`` (reference:
py-clob-client-v2 fees.py). The per-market (rate, exponent) come from the
feeSchedule at runtime; these tests pin the formula, not category constants.
"""

from decimal import Decimal

from polymarket.models import Side
from polymarket.utils.fees import (
    calculate_net_cost,
    calculate_order_fee,
    calculate_profit_after_fees,
    compare_fees_buy_vs_sell,
    estimate_breakeven_exit,
    get_effective_spread,
)


def test_zero_bps_is_fee_free():
    assert calculate_order_fee(Side.BUY, Decimal("0.5"), Decimal("100"), 0) == Decimal("0.0")


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
    sell_net, sell_fee = calculate_net_cost(Side.SELL, Decimal("0.5"), Decimal("100"), 312)
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


def test_fee_comparison_uses_requested_nonzero_rate():
    comparison = compare_fees_buy_vs_sell(
        Decimal("0.5"),
        Decimal("100"),
        312,
    )

    assert comparison["buy_fee"] == Decimal("1.560000")
    assert comparison["sell_fee"] == Decimal("1.560000")
    assert comparison["buy_fee_pct_of_cost"] > 0
    assert comparison["sell_fee_pct_of_proceeds"] > 0


def test_round_trip_profit_deducts_entry_and_exit_curve_fees():
    result = calculate_profit_after_fees(
        Side.BUY,
        Decimal("0.5"),
        Decimal("0.6"),
        Decimal("100"),
        312,
        312,
    )
    expected_entry_fee = calculate_order_fee(
        Side.BUY,
        Decimal("0.5"),
        Decimal("100"),
        312,
    )
    expected_exit_fee = calculate_order_fee(
        Side.SELL,
        Decimal("0.6"),
        Decimal("120"),
        312,
    )

    assert result["entry_fee"] == expected_entry_fee
    assert result["exit_fee"] == expected_exit_fee
    assert result["total_fees"] == expected_entry_fee + expected_exit_fee
    assert result["net_profit"] == result["gross_profit"] - result["total_fees"]


def test_breakeven_moves_to_recover_both_fee_legs():
    breakeven, total_fees = estimate_breakeven_exit(
        Side.BUY,
        Decimal("0.5"),
        Decimal("100"),
        312,
        312,
    )
    result = calculate_profit_after_fees(
        Side.BUY,
        Decimal("0.5"),
        breakeven,
        Decimal("100"),
        312,
        312,
    )

    assert breakeven > Decimal("0.5")
    assert total_fees > 0
    assert abs(result["net_profit"]) < Decimal("0.03")


def test_effective_spread_includes_both_fee_legs():
    no_fee = get_effective_spread(
        Decimal("0.49"),
        Decimal("0.51"),
        Decimal("100"),
        0,
    )
    with_fee = get_effective_spread(
        Decimal("0.49"),
        Decimal("0.51"),
        Decimal("100"),
        312,
    )

    assert with_fee["buy_cost"] > no_fee["buy_cost"]
    assert with_fee["sell_proceeds"] < no_fee["sell_proceeds"]
    assert with_fee["effective_spread"] > no_fee["effective_spread"]
