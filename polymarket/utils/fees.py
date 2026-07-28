"""Fee calculation and profitability utilities for Polymarket.

Markets can expose a Gamma ``feeSchedule`` with a rate and exponent.  The
economic taker fee is:

``shares × rate × (price × (1 - price)) ** exponent``

The public helpers accept USD notional as ``size``. Fees are rounded to five
decimal places, matching the current Polymarket fee contract; values below one
full fee quantum are zero.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Tuple

from ..models import Side

FEE_QUANTUM = Decimal("0.00001")
AMOUNT_QUANTUM = Decimal("0.000001")
PRICE_QUANTUM = Decimal("0.0001")


def calculate_order_fee(
    side: Side,
    price: Decimal,
    size: Decimal,
    fee_rate_bps: int = 0,
    fee_exponent: Decimal = Decimal("1"),
) -> Decimal:
    """Return the taker fee for ``size`` USD notional at ``price``."""
    del side  # The current curve is symmetric across BUY and SELL.
    price = Decimal(str(price))
    size = Decimal(str(size))
    exponent = Decimal(str(fee_exponent))
    if fee_rate_bps <= 0 or size <= 0:
        return Decimal("0.0")
    if not Decimal("0") < price < Decimal("1"):
        raise ValueError(f"Fee price must be strictly between 0 and 1: {price}")
    if exponent <= 0 or not exponent.is_finite():
        raise ValueError(f"Fee exponent must be positive and finite: {exponent}")

    rate = Decimal(fee_rate_bps) / Decimal("10000")
    shares = size / price
    curve = (price * (Decimal("1") - price)) ** exponent
    fee = shares * rate * curve
    if fee < FEE_QUANTUM:
        return Decimal("0.00000")
    return fee.quantize(FEE_QUANTUM, rounding=ROUND_HALF_UP)


def calculate_net_cost(
    side: Side,
    price: Decimal,
    size: Decimal,
    fee_rate_bps: int = 0,
    fee_exponent: Decimal = Decimal("1"),
) -> Tuple[Decimal, Decimal]:
    """Return total BUY cost or net SELL proceeds plus the fee component."""
    size = Decimal(str(size))
    fee = calculate_order_fee(side, price, size, fee_rate_bps, fee_exponent)
    net_amount = size + fee if side == Side.BUY else size - fee
    return net_amount.quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP), fee


def compare_fees_buy_vs_sell(
    price: Decimal,
    size: Decimal,
    fee_rate_bps: int = 0,
    fee_exponent: Decimal = Decimal("1"),
) -> Dict[str, Any]:
    """Compare the same-notional BUY and SELL fee legs."""
    buy_net, buy_fee = calculate_net_cost(
        Side.BUY, price, size, fee_rate_bps, fee_exponent
    )
    sell_net, sell_fee = calculate_net_cost(
        Side.SELL, price, size, fee_rate_bps, fee_exponent
    )
    size = Decimal(str(size))
    return {
        "buy_fee": buy_fee,
        "sell_fee": sell_fee,
        "fee_difference": buy_fee - sell_fee,
        "buy_fee_pct_of_cost": (
            buy_fee / buy_net * Decimal("100") if buy_net > 0 else Decimal("0.0")
        ),
        "sell_fee_pct_of_proceeds": (
            sell_fee / sell_net * Decimal("100") if sell_net > 0 else Decimal("0.0")
        ),
    }


def calculate_profit_after_fees(
    entry_side: Side,
    entry_price: Decimal,
    exit_price: Decimal,
    size: Decimal,
    entry_fee_rate_bps: int = 0,
    exit_fee_rate_bps: int = 0,
    entry_fee_exponent: Decimal = Decimal("1"),
    exit_fee_exponent: Decimal = Decimal("1"),
) -> Dict[str, Any]:
    """Calculate round-trip P&L from an entry USD notional."""
    entry_price = Decimal(str(entry_price))
    exit_price = Decimal(str(exit_price))
    size = Decimal(str(size))
    if not Decimal("0") < entry_price < Decimal("1"):
        raise ValueError(f"Entry price must be strictly between 0 and 1: {entry_price}")
    if not Decimal("0") < exit_price < Decimal("1"):
        raise ValueError(f"Exit price must be strictly between 0 and 1: {exit_price}")

    token_count = size / entry_price
    exit_notional = token_count * exit_price
    entry_fee = calculate_order_fee(
        entry_side,
        entry_price,
        size,
        entry_fee_rate_bps,
        entry_fee_exponent,
    )
    exit_side = Side.SELL if entry_side == Side.BUY else Side.BUY
    exit_fee = calculate_order_fee(
        exit_side,
        exit_price,
        exit_notional,
        exit_fee_rate_bps,
        exit_fee_exponent,
    )

    if entry_side == Side.BUY:
        gross_profit = exit_notional - size
        entry_cost = size + entry_fee
        exit_proceeds = exit_notional - exit_fee
    else:
        gross_profit = size - exit_notional
        entry_cost = exit_notional + exit_fee
        exit_proceeds = size - entry_fee

    total_fees = entry_fee + exit_fee
    net_profit = gross_profit - total_fees
    roi_pct = (
        net_profit / entry_cost * Decimal("100")
        if entry_cost > 0
        else Decimal("0.0")
    )
    return {
        "gross_profit": gross_profit.quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP),
        "entry_fee": entry_fee,
        "exit_fee": exit_fee,
        "total_fees": total_fees,
        "net_profit": net_profit.quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP),
        "roi_pct": roi_pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "entry_cost": entry_cost.quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP),
        "exit_proceeds": exit_proceeds.quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP),
        "token_count": token_count.quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP),
    }


def estimate_breakeven_exit(
    entry_side: Side,
    entry_price: Decimal,
    entry_size: Decimal,
    entry_fee_rate_bps: int = 0,
    exit_fee_rate_bps: int = 0,
    entry_fee_exponent: Decimal = Decimal("1"),
    exit_fee_exponent: Decimal = Decimal("1"),
) -> Tuple[Decimal, Decimal]:
    """Find the exit price where the fee-aware round trip reaches zero P&L."""
    entry_price = Decimal(str(entry_price))
    if entry_fee_rate_bps == 0 and exit_fee_rate_bps == 0:
        return entry_price.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP), Decimal("0.0")

    lower = Decimal("0.0000001")
    upper = Decimal("0.9999999")
    for _ in range(80):
        midpoint = (lower + upper) / Decimal("2")
        net_profit = calculate_profit_after_fees(
            entry_side,
            entry_price,
            midpoint,
            entry_size,
            entry_fee_rate_bps,
            exit_fee_rate_bps,
            entry_fee_exponent,
            exit_fee_exponent,
        )["net_profit"]
        if entry_side == Side.BUY:
            if net_profit < 0:
                lower = midpoint
            else:
                upper = midpoint
        else:
            if net_profit < 0:
                upper = midpoint
            else:
                lower = midpoint

    breakeven = ((lower + upper) / Decimal("2")).quantize(
        PRICE_QUANTUM, rounding=ROUND_HALF_UP
    )
    result = calculate_profit_after_fees(
        entry_side,
        entry_price,
        breakeven,
        entry_size,
        entry_fee_rate_bps,
        exit_fee_rate_bps,
        entry_fee_exponent,
        exit_fee_exponent,
    )
    return breakeven, result["total_fees"]


def get_effective_spread(
    bid: Decimal,
    ask: Decimal,
    size: Decimal,
    fee_rate_bps: int = 0,
    fee_exponent: Decimal = Decimal("1"),
) -> Dict[str, Any]:
    """Calculate immediate ask-to-bid spread after both taker-fee legs."""
    bid = Decimal(str(bid))
    ask = Decimal(str(ask))
    size = Decimal(str(size))
    raw_spread = ask - bid
    midpoint = (bid + ask) / Decimal("2")

    buy_notional = ask * size
    sell_notional = bid * size
    buy_fee = calculate_order_fee(
        Side.BUY, ask, buy_notional, fee_rate_bps, fee_exponent
    )
    sell_fee = calculate_order_fee(
        Side.SELL, bid, sell_notional, fee_rate_bps, fee_exponent
    )
    buy_cost = buy_notional + buy_fee
    sell_proceeds = sell_notional - sell_fee
    effective_spread = buy_cost - sell_proceeds

    raw_spread_bps = (
        int(
            (raw_spread / midpoint * Decimal("10000")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        if midpoint > 0
        else 0
    )
    effective_spread_bps = (
        int(
            (effective_spread / buy_cost * Decimal("10000")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        if buy_cost > 0
        else 0
    )
    return {
        "raw_spread": raw_spread.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP),
        "raw_spread_bps": raw_spread_bps,
        "buy_cost": buy_cost.quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP),
        "sell_proceeds": sell_proceeds.quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP),
        "effective_spread": effective_spread.quantize(
            AMOUNT_QUANTUM, rounding=ROUND_HALF_UP
        ),
        "effective_spread_bps": effective_spread_bps,
    }
