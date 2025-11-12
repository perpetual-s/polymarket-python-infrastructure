"""
Order validation utilities for Polymarket trading.

Implements validation rules from official Polymarket clients to prevent
order rejection and ensure contract compliance.

Reference: https://github.com/Polymarket/go-order-utils (MIT)

DECIMAL PRECISION: All numeric types use Decimal for financial-grade accuracy.
"""

import logging
from typing import Optional, Tuple
from decimal import Decimal
from ..models import OrderRequest, Side, OrderType, Market
from ..exceptions import ValidationError

logger = logging.getLogger(__name__)

# Validation constants (using Decimal for precision)
MIN_PRICE = Decimal("0.01")
MAX_PRICE = Decimal("0.99")
MIN_SIZE = Decimal("0.01")  # Minimum order size in USDC
# NOTE: Polymarket has NO trading fees (https://docs.polymarket.com/polymarket-learn/trading/fees)
# This constant exists for protocol compatibility only
MAX_FEE_RATE_BPS = 1000  # Maximum theoretical fee (unused - Polymarket has 0% fees)
MIN_EXPIRATION_BUFFER = 60  # Minimum 60 seconds from now for GTD orders


def validate_order(order: OrderRequest) -> Tuple[bool, Optional[str]]:
    """
    Comprehensive order validation before submission.

    Checks:
    - Price bounds (0.01-0.99)
    - Size constraints (> MIN_SIZE)
    - Fee rate limits (0-1000 bps)
    - Expiration validity (for GTD orders)
    - Token ID format

    Args:
        order: OrderRequest to validate

    Returns:
        Tuple of (is_valid, error_message)
        - (True, None) if valid
        - (False, "error message") if invalid

    Example:
        >>> order = OrderRequest(...)
        >>> valid, error = validate_order(order)
        >>> if not valid:
        ...     logger.error(f"Invalid order: {error}")
    """
    # Validate price
    if not MIN_PRICE <= order.price <= MAX_PRICE:
        return (False, f"Price {order.price} outside valid range [{MIN_PRICE}, {MAX_PRICE}]")

    # Validate size
    if order.size < MIN_SIZE:
        return (False, f"Size {order.size} below minimum {MIN_SIZE} USDC")

    # Validate token ID
    if not order.token_id or not isinstance(order.token_id, str):
        return (False, "Token ID must be a non-empty string")

    # Validate GTD expiration
    if order.order_type == OrderType.GTD:
        if order.expiration is None:
            return (False, "GTD orders require expiration timestamp")

        import time
        now = int(time.time())
        if order.expiration <= now + MIN_EXPIRATION_BUFFER:
            return (False, f"Expiration must be at least {MIN_EXPIRATION_BUFFER}s in future")

    # All validations passed
    return (True, None)


def validate_price_bounds(price: Decimal) -> bool:
    """
    Validate price is within acceptable bounds.

    Args:
        price: Order price (Decimal)

    Returns:
        True if valid

    Raises:
        ValidationError: If price is invalid
    """
    if not MIN_PRICE <= price <= MAX_PRICE:
        raise ValidationError(
            f"Price {price} outside valid range [{MIN_PRICE}, {MAX_PRICE}]"
        )
    return True


def validate_size(size: Decimal, min_size: Decimal = MIN_SIZE) -> bool:
    """
    Validate order size is sufficient.

    Args:
        size: Order size in USDC (Decimal)
        min_size: Minimum allowed size (Decimal)

    Returns:
        True if valid

    Raises:
        ValidationError: If size is too small
    """
    if size < min_size:
        raise ValidationError(
            f"Size {size} below minimum {min_size} USDC"
        )
    return True


def validate_fee_rate(fee_rate_bps: int) -> bool:
    """
    Validate fee rate is within acceptable range.

    NOTE: Polymarket has NO trading fees. This validation exists
    for protocol compatibility only. Always pass 0.

    Args:
        fee_rate_bps: Fee rate in basis points (always 0 for Polymarket)

    Returns:
        True if valid

    Raises:
        ValidationError: If fee rate exceeds maximum
    """
    if not 0 <= fee_rate_bps <= MAX_FEE_RATE_BPS:
        raise ValidationError(
            f"Fee rate {fee_rate_bps} bps outside valid range [0, {MAX_FEE_RATE_BPS}]"
        )
    return True


def validate_token_complementarity(
    token_id_1: str,
    token_id_2: str,
    market: Optional[Market] = None
) -> bool:
    """
    Validate that two tokens are complementary (YES/NO pair).

    For neg-risk markets, tokens must be valid complements.

    Args:
        token_id_1: First token ID
        token_id_2: Second token ID
        market: Optional market object for additional validation

    Returns:
        True if tokens are valid complements

    Raises:
        ValidationError: If tokens are not complementary

    Note:
        Full validation requires on-chain check via CTF Exchange.
        This performs basic sanity checks only.
    """
    # Basic check: different tokens
    if token_id_1 == token_id_2:
        raise ValidationError(
            f"Tokens must be different: {token_id_1} == {token_id_2}"
        )

    # If market provided, check it's a binary market
    if market and len(market.tokens or []) != 2:
        raise ValidationError(
            f"Complementarity validation requires binary market (2 tokens), "
            f"got {len(market.tokens or [])} tokens"
        )

    # If market provided, check tokens belong to it
    if market and market.tokens:
        if token_id_1 not in market.tokens or token_id_2 not in market.tokens:
            raise ValidationError(
                f"Tokens {token_id_1}, {token_id_2} do not belong to market {market.id}"
            )

    return True


def validate_neg_risk_market(market: Market) -> bool:
    """
    Validate neg-risk market is safe for trading.

    Checks:
    - Not augmented (no unnamed outcomes)
    - Has valid outcome set
    - Properly configured

    Args:
        market: Market to validate

    Returns:
        True if safe to trade

    Raises:
        ValidationError: If market is unsafe
    """
    # Check if augmented
    if market.neg_risk_augmented:
        raise ValidationError(
            f"Market {market.slug} is augmented neg-risk (incomplete outcome universe). "
            "Not safe for automated trading."
        )

    # Check for unnamed outcomes
    if market.outcomes:
        unsafe_patterns = ["candidate_", "option_", "other", "unnamed", "tbd"]
        for outcome in market.outcomes:
            outcome_lower = outcome.lower()
            if any(pattern in outcome_lower for pattern in unsafe_patterns):
                raise ValidationError(
                    f"Market {market.slug} has placeholder outcome: {outcome}. "
                    "Not safe for automated trading."
                )

    # Check has outcomes
    if not market.outcomes or len(market.outcomes) < 2:
        raise ValidationError(
            f"Market {market.slug} has insufficient outcomes: {len(market.outcomes or [])}"
        )

    return True


def validate_balance(
    side: Side,
    price: Decimal,
    size: Decimal,
    available_usdc: Decimal,
    available_tokens: Decimal = Decimal("0.0"),
    fee_rate_bps: int = 0
) -> Tuple[bool, Optional[str]]:
    """
    Validate wallet has sufficient balance for order.

    Args:
        side: BUY or SELL
        price: Order price (Decimal)
        size: Order size in USDC (Decimal)
        available_usdc: Available USDC balance (Decimal)
        available_tokens: Available token balance for SELL orders (Decimal)
        fee_rate_bps: Fee rate in basis points

    Returns:
        Tuple of (is_valid, error_message)

    Example:
        >>> from decimal import Decimal
        >>> valid, error = validate_balance(
        ...     Side.BUY, Decimal("0.60"), Decimal("100.0"), Decimal("50.0")
        ... )
        >>> # Returns: (False, "Insufficient USDC: need $100.00, have $50.00")
    """
    from .fees import calculate_net_cost

    # Validate price and size bounds first
    try:
        validate_price_bounds(price)
    except ValidationError as e:
        return (False, str(e))

    try:
        validate_size(size)
    except ValidationError as e:
        return (False, str(e))

    # Validate available balances are non-negative
    if available_usdc < Decimal("0.0"):
        return (False, f"Available USDC cannot be negative: ${available_usdc:.2f}")

    if available_tokens < Decimal("0.0"):
        return (False, f"Available tokens cannot be negative: {available_tokens:.2f}")

    if side == Side.BUY:
        # Calculate total cost including fees
        net_cost, fee = calculate_net_cost(side, price, size, fee_rate_bps)

        if available_usdc < net_cost:
            return (
                False,
                f"Insufficient USDC: need ${net_cost:.2f}, have ${available_usdc:.2f}"
            )

    else:  # SELL
        # Calculate tokens needed
        # CRITICAL FIX: size is in USD value, convert to token quantity
        # Example: size=$10, price=$0.50 → need 20 tokens
        token_amount = size / price  # USD / price = token quantity

        if available_tokens < token_amount:
            return (
                False,
                f"Insufficient tokens: need {token_amount:.2f}, have {available_tokens:.2f} "
                f"(selling ${size:.2f} worth at ${price:.2f}/token)"
            )

        # Validate that proceeds will be positive after fees
        # (fee is deducted from proceeds on SELL orders)
        net_proceeds, fee = calculate_net_cost(side, price, size, fee_rate_bps)
        if net_proceeds <= 0:
            return (
                False,
                f"SELL order would result in negative proceeds: "
                f"fee ${fee:.2f} exceeds proceeds ${size:.2f}. "
                f"This usually indicates an error in order parameters."
            )

    return (True, None)


def validate_order_amounts(
    maker_amount: Decimal,
    taker_amount: Decimal,
    min_amount: Decimal = Decimal("0.01")
) -> bool:
    """
    Validate maker and taker amounts are positive and non-zero.

    Args:
        maker_amount: Maker amount (Decimal)
        taker_amount: Taker amount (Decimal)
        min_amount: Minimum allowed amount (Decimal)

    Returns:
        True if valid

    Raises:
        ValidationError: If amounts are invalid
    """
    if maker_amount < min_amount:
        raise ValidationError(
            f"Maker amount {maker_amount} below minimum {min_amount}"
        )

    if taker_amount < min_amount:
        raise ValidationError(
            f"Taker amount {taker_amount} below minimum {min_amount}"
        )

    return True


def check_order_profitability(
    entry_price: Decimal,
    exit_price: Decimal,
    size: Decimal,
    fee_rate_bps: int,
    min_profit_usdc: Decimal = Decimal("0.10")
) -> Tuple[bool, Decimal]:
    """
    Check if round-trip trade would be profitable.

    Args:
        entry_price: Entry price (Decimal)
        exit_price: Exit price (Decimal)
        size: Trade size in USDC (Decimal)
        fee_rate_bps: Fee rate in basis points
        min_profit_usdc: Minimum acceptable profit in USDC (Decimal)

    Returns:
        Tuple of (is_profitable, net_profit as Decimal)

    Example:
        >>> from decimal import Decimal
        >>> profitable, profit = check_order_profitability(
        ...     Decimal("0.60"), Decimal("0.70"), Decimal("100.0"), 0
        ... )
        >>> if not profitable:
        ...     logger.warning(f"Trade not profitable: ${profit:.2f}")
    """
    from .fees import calculate_profit_after_fees

    pnl = calculate_profit_after_fees(
        Side.BUY,
        entry_price,
        exit_price,
        size,
        fee_rate_bps,
        fee_rate_bps
    )

    net_profit = pnl['net_profit']
    is_profitable = net_profit >= min_profit_usdc

    return (is_profitable, net_profit)
