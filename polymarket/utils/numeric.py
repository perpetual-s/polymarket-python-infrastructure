"""
Numeric type utilities for Decimal precision.

Helper functions for safe conversion between types while maintaining
financial-grade precision.
"""

from typing import Any, Optional
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import logging

logger = logging.getLogger(__name__)


def to_decimal(value: Any, default: Optional[Decimal] = None) -> Optional[Decimal]:
    """
    Safely convert any value to Decimal.

    Args:
        value: Value to convert (str, int, float, Decimal, None)
        default: Default value if conversion fails (default: None)

    Returns:
        Decimal or default if conversion fails

    Examples:
        >>> to_decimal("0.65")
        Decimal('0.65')
        >>> to_decimal(100.50)
        Decimal('100.5')
        >>> to_decimal(None, Decimal("0"))
        Decimal('0')
    """
    if value is None:
        return default

    try:
        if isinstance(value, Decimal):
            return value
        elif isinstance(value, str):
            # Direct string conversion (most precise)
            return Decimal(value)
        elif isinstance(value, (int, float)):
            # Convert via string to avoid float precision loss
            return Decimal(str(value))
        else:
            logger.warning(f"Cannot convert {type(value)} to Decimal: {value}")
            return default
    except (ValueError, InvalidOperation) as e:
        logger.warning(f"Failed to convert {value} to Decimal: {e}")
        return default


def to_wei(amount: Decimal, decimals: int = 6) -> int:
    """
    Convert token amount to smallest unit (wei).

    Args:
        amount: Amount in token units (Decimal)
        decimals: Number of decimals (default: 6 for USDC/CTF)

    Returns:
        Amount in wei (int)

    Examples:
        >>> to_wei(Decimal("100.50"))
        100500000
        >>> to_wei(Decimal("0.33"), 6)
        330000
    """
    multiplier = Decimal(10) ** decimals
    wei = amount * multiplier
    # Round to nearest integer (banker's rounding)
    return int(wei.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_wei(wei: int, decimals: int = 6) -> Decimal:
    """
    Convert wei (smallest unit) to token amount.

    Args:
        wei: Amount in wei (int)
        decimals: Number of decimals (default: 6 for USDC/CTF)

    Returns:
        Amount in token units (Decimal)

    Examples:
        >>> from_wei(100500000)
        Decimal('100.5')
        >>> from_wei(330000, 6)
        Decimal('0.33')
    """
    divisor = Decimal(10) ** decimals
    return Decimal(wei) / divisor


def quantize_price(price: Decimal, tick_size: Decimal = Decimal("0.01")) -> Decimal:
    """
    Round price to tick size.

    Args:
        price: Price to round
        tick_size: Minimum price increment (default: 0.01)

    Returns:
        Rounded price

    Examples:
        >>> quantize_price(Decimal("0.655"))
        Decimal('0.66')
        >>> quantize_price(Decimal("0.333"), Decimal("0.01"))
        Decimal('0.33')
    """
    return price.quantize(tick_size, rounding=ROUND_HALF_UP)


def quantize_size(size: Decimal, decimals: int = 2) -> Decimal:
    """
    Round size to specified decimals.

    Args:
        size: Size to round
        decimals: Number of decimal places (default: 2)

    Returns:
        Rounded size

    Examples:
        >>> quantize_size(Decimal("100.555"))
        Decimal('100.56')
        >>> quantize_size(Decimal("0.9999"), 2)
        Decimal('1.00')
    """
    quantizer = Decimal(10) ** -decimals
    return size.quantize(quantizer, rounding=ROUND_HALF_UP)


def decimal_to_str(value: Decimal, strip: bool = True) -> str:
    """
    Convert Decimal to string representation.

    Args:
        value: Decimal to convert
        strip: Strip trailing zeros (default: True)

    Returns:
        String representation

    Examples:
        >>> decimal_to_str(Decimal("100.50"))
        '100.5'
        >>> decimal_to_str(Decimal("100.50"), strip=False)
        '100.50'
    """
    s = str(value)
    if strip and '.' in s:
        # Remove trailing zeros and decimal point if not needed
        s = s.rstrip('0').rstrip('.')
    return s


def parse_api_numeric(value: Any, field_name: str = "value") -> Decimal:
    """
    Parse numeric value from API response.

    Handles both string and numeric JSON responses.

    Args:
        value: Value from API (str, int, float, None)
        field_name: Field name for error messages

    Returns:
        Decimal value

    Raises:
        ValueError: If value cannot be parsed

    Examples:
        >>> parse_api_numeric("0.65")
        Decimal('0.65')
        >>> parse_api_numeric(100)
        Decimal('100')
        >>> parse_api_numeric(None, "price")
        Traceback (most recent call last):
        ...
        ValueError: Missing required field: price
    """
    if value is None:
        raise ValueError(f"Missing required field: {field_name}")

    result = to_decimal(value)
    if result is None:
        raise ValueError(f"Invalid numeric value for {field_name}: {value}")

    return result
