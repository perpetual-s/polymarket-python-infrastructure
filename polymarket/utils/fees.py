"""
Fee calculation and profitability utilities for Polymarket.

IMPORTANT: Polymarket does NOT charge trading fees (0% fees officially confirmed).
All fee calculations return Decimal("0.0").

Source: https://docs.polymarket.com/polymarket-learn/trading/fees
"Polymarket does not charge any type of fee."

WHY THIS MODULE EXISTS (Despite 0% Fees):

These functions provide real utility beyond fee calculation:

1. **calculate_profit_after_fees** - Complete P&L breakdown
   - Calculates token count, gross profit, net profit, ROI
   - Essential for profitability analysis and trade planning
   - Used by: check_order_profitability(), Example 10

2. **calculate_net_cost** - Order cost calculation
   - Returns total USDC needed (BUY) or received (SELL)
   - Essential for balance validation
   - Used by: validate_balance() (critical path)

3. **get_effective_spread** - Market making spread analysis
   - Calculates round-trip cost (buy at ask, sell at bid)
   - Critical for spread farming strategies (Strategy-1)

4. **estimate_breakeven_exit** - Breakeven price calculation
   - With 0% fees: breakeven = entry_price
   - Provides consistent API for trade planning

5. **compare_fees_buy_vs_sell** - Fee comparison (returns zeros)
   - API completeness, future-proofing

6. **calculate_order_fee** - Individual fee calculation (returns zero)
   - API completeness, future-proofing

FUTURE-PROOFING:
- Polymarket might add fees in the future
- Easy to support other prediction markets (Kalshi has fees)
- Consistent API across platforms

DECIMAL PRECISION: All numeric types use Decimal for financial-grade accuracy.
"""

import logging
from typing import Tuple, Dict, Any
from decimal import Decimal, ROUND_HALF_UP
from ..models import Side

logger = logging.getLogger(__name__)


def calculate_order_fee(
    side: Side,
    price: Decimal,
    size: Decimal,
    fee_rate_bps: int = 0
) -> Decimal:
    """
    Calculate fee for an order.

    Polymarket charges NO trading fees.
    This function exists for API compatibility only.

    Args:
        side: BUY or SELL (unused)
        price: Order price (unused)
        size: Order size in USDC (unused)
        fee_rate_bps: Fee rate in basis points (unused, always 0)

    Returns:
        Decimal("0.0") (Polymarket has no trading fees)

    Example:
        >>> from decimal import Decimal
        >>> calculate_order_fee(Side.BUY, Decimal("0.60"), Decimal("100.0"), 0)
        Decimal('0.0')
    """
    return Decimal("0.0")


def calculate_net_cost(
    side: Side,
    price: Decimal,
    size: Decimal,
    fee_rate_bps: int = 0
) -> Tuple[Decimal, Decimal]:
    """
    Calculate net cost/proceeds including fees.

    Polymarket charges NO trading fees.
    Net cost = size (no fees added).

    Args:
        side: BUY or SELL
        price: Order price
        size: Order size in USDC
        fee_rate_bps: Fee rate in basis points (unused, always 0)

    Returns:
        Tuple of (net_amount, fee):
        - BUY: net_amount = size (cost, no fees)
        - SELL: net_amount = size (proceeds, no fees)
        - fee = Decimal("0.0") (always)

    Example:
        >>> from decimal import Decimal
        >>> calculate_net_cost(Side.BUY, Decimal("0.60"), Decimal("100.0"), 0)
        (Decimal('100.000000'), Decimal('0.0'))  # Need $100 total (no fees)

        >>> calculate_net_cost(Side.SELL, Decimal("0.60"), Decimal("100.0"), 0)
        (Decimal('100.000000'), Decimal('0.0'))  # Receive $100 (no fees)
    """
    fee = Decimal("0.0")
    # Quantize to 6 decimal places for USDC precision
    net_amount = size.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    return (net_amount, fee)


def compare_fees_buy_vs_sell(
    price: Decimal,
    size: Decimal,
    fee_rate_bps: int = 0
) -> Dict[str, Any]:
    """
    Compare fees for buying vs selling at same price.

    Polymarket charges NO trading fees.
    All fees are 0.

    Args:
        price: Order price (unused)
        size: Order size in USDC (unused)
        fee_rate_bps: Fee rate in basis points (unused)

    Returns:
        Dict with zero fees (values are Decimal)

    Example:
        >>> from decimal import Decimal
        >>> compare_fees_buy_vs_sell(Decimal("0.60"), Decimal("100.0"), 0)
        {
            'buy_fee': Decimal('0.0'),
            'sell_fee': Decimal('0.0'),
            'fee_difference': Decimal('0.0'),
            'buy_fee_pct_of_cost': Decimal('0.0'),
            'sell_fee_pct_of_proceeds': Decimal('0.0')
        }
    """
    return {
        'buy_fee': Decimal("0.0"),
        'sell_fee': Decimal("0.0"),
        'fee_difference': Decimal("0.0"),
        'buy_fee_pct_of_cost': Decimal("0.0"),
        'sell_fee_pct_of_proceeds': Decimal("0.0"),
    }


def estimate_breakeven_exit(
    entry_side: Side,
    entry_price: Decimal,
    entry_size: Decimal,
    entry_fee_rate_bps: int = 0,
    exit_fee_rate_bps: int = 0
) -> Tuple[Decimal, Decimal]:
    """
    Calculate breakeven exit price including fees.

    Polymarket charges NO trading fees.
    Breakeven = entry price (no fees to recover).

    Args:
        entry_side: BUY or SELL
        entry_price: Entry price
        entry_size: Position size (unused)
        entry_fee_rate_bps: Fee rate for entry (unused)
        exit_fee_rate_bps: Fee rate for exit (unused)

    Returns:
        Tuple of (breakeven_price, total_fees):
        - breakeven_price = entry_price (no fees to recover)
        - total_fees = Decimal("0.0") (always)

    Example:
        >>> from decimal import Decimal
        >>> estimate_breakeven_exit(Side.BUY, Decimal("0.60"), Decimal("100.0"), 0, 0)
        (Decimal('0.6000'), Decimal('0.0'))  # Breakeven at entry price, no fees
    """
    # Quantize to 4 decimal places for price precision
    breakeven = entry_price.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return (breakeven, Decimal("0.0"))


def calculate_profit_after_fees(
    entry_side: Side,
    entry_price: Decimal,
    exit_price: Decimal,
    size: Decimal,
    entry_fee_rate_bps: int = 0,
    exit_fee_rate_bps: int = 0
) -> Dict[str, Any]:
    """
    Calculate profit/loss after all fees for a round-trip trade.

    Polymarket charges NO trading fees.
    Net profit = gross profit (no fees deducted).

    Args:
        entry_side: BUY or SELL
        entry_price: Entry price
        exit_price: Exit price
        size: Trading size in USDC
        entry_fee_rate_bps: Fee rate for entry (unused)
        exit_fee_rate_bps: Fee rate for exit (unused)

    Returns:
        Dict with profit metrics (all Decimal values, no fees)

    Example:
        >>> from decimal import Decimal
        >>> calculate_profit_after_fees(
        ...     Side.BUY,
        ...     Decimal("0.60"),
        ...     Decimal("0.70"),
        ...     Decimal("100.0"),
        ...     0, 0
        ... )
        {
            'gross_profit': Decimal('16.666667'),   # 166.67 tokens × $0.10 price increase
            'entry_fee': Decimal('0.0'),
            'exit_fee': Decimal('0.0'),
            'total_fees': Decimal('0.0'),
            'net_profit': Decimal('16.666667'),     # Same as gross (no fees)
            'roi_pct': Decimal('16.67'),
            'entry_cost': Decimal('100.000000'),
            'exit_proceeds': Decimal('116.666667'),
            'token_count': Decimal('166.666667')
        }
    """
    # Calculate token quantity from entry
    token_count = size / entry_price

    # Calculate gross profit from price movement
    if entry_side == Side.BUY:
        # Bought tokens, selling them at different price
        gross_profit = token_count * (exit_price - entry_price)
        entry_cost = size
        exit_proceeds = token_count * exit_price
    else:
        # Sold tokens, buying them back at different price
        gross_profit = token_count * (entry_price - exit_price)
        entry_cost = token_count * exit_price
        exit_proceeds = size

    # NO FEES on Polymarket
    entry_fee = Decimal("0.0")
    exit_fee = Decimal("0.0")
    total_fees = Decimal("0.0")

    # Net profit = gross profit (no fees deducted)
    net_profit = gross_profit

    # Calculate ROI
    if entry_cost > 0:
        roi_pct = (net_profit / entry_cost) * Decimal("100")
    else:
        roi_pct = Decimal("0.0")

    return {
        'gross_profit': gross_profit.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        'entry_fee': Decimal("0.0"),
        'exit_fee': Decimal("0.0"),
        'total_fees': Decimal("0.0"),
        'net_profit': net_profit.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        'roi_pct': roi_pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        'entry_cost': entry_cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        'exit_proceeds': exit_proceeds.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        'token_count': token_count.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
    }


def get_effective_spread(
    bid: Decimal,
    ask: Decimal,
    size: Decimal,
    fee_rate_bps: int = 0
) -> Dict[str, Any]:
    """
    Calculate effective spread including fees.

    Polymarket charges NO trading fees.
    Effective spread = raw spread (no fees added).

    Args:
        bid: Best bid price
        ask: Best ask price
        size: Trading size
        fee_rate_bps: Fee rate in basis points (unused)

    Returns:
        Dict with spread metrics (numeric values are Decimal, bps are int)

    Example:
        >>> from decimal import Decimal
        >>> get_effective_spread(Decimal("0.59"), Decimal("0.61"), Decimal("100.0"), 0)
        {
            'raw_spread': Decimal('0.0200'),
            'raw_spread_bps': 200,
            'buy_cost': Decimal('61.000000'),       # 0.61 × 100 (no fees)
            'sell_proceeds': Decimal('59.000000'),  # 0.59 × 100 (no fees)
            'effective_spread': Decimal('2.000000'),
            'effective_spread_bps': 328
        }
    """
    raw_spread = ask - bid
    midpoint = (bid + ask) / Decimal("2")

    # Calculate cost to buy at ask (no fees)
    buy_cost = ask * size

    # Calculate proceeds from selling at bid (no fees)
    sell_proceeds = bid * size

    # Effective spread = cost to buy and immediately sell (no fees added)
    effective_spread = buy_cost - sell_proceeds

    # Calculate basis points (must be int)
    if midpoint > 0:
        raw_spread_bps = int((raw_spread / midpoint * Decimal("10000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    else:
        raw_spread_bps = 0

    if buy_cost > 0:
        effective_spread_bps = int((effective_spread / buy_cost * Decimal("10000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    else:
        effective_spread_bps = 0

    return {
        'raw_spread': raw_spread.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
        'raw_spread_bps': raw_spread_bps,
        'buy_cost': buy_cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        'sell_proceeds': sell_proceeds.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        'effective_spread': effective_spread.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        'effective_spread_bps': effective_spread_bps,
    }
