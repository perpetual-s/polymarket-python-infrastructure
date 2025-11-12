"""
Input validation utilities.

Validates orders, prices, and other inputs before API calls.
"""

import re
import time
from typing import Optional, Any
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from ..exceptions import ValidationError, OrderExpiredError


MIN_PRICE = Decimal("0.01")
MAX_PRICE = Decimal("0.99")
MIN_SIZE = Decimal("1.0")  # USDC
PRICE_DECIMALS = Decimal("0.01")  # 2 decimal places
SIZE_DECIMALS = Decimal("0.01")   # 2 decimal places


def validate_price(price: Any) -> Decimal:
    """
    Validate and normalize price.

    Args:
        price: Order price (float, int, str, or Decimal)

    Returns:
        Normalized price (Decimal)

    Raises:
        ValidationError: If price is invalid
    """
    # Convert to Decimal
    try:
        if isinstance(price, Decimal):
            price_dec = price
        elif isinstance(price, str):
            price_dec = Decimal(price)
        elif isinstance(price, (int, float)):
            price_dec = Decimal(str(price))
        else:
            raise ValidationError(f"Price must be numeric, got {type(price)}")
    except (ValueError, InvalidOperation) as e:
        raise ValidationError(f"Invalid price format: {price}") from e

    # Range check
    if not (MIN_PRICE <= price_dec <= MAX_PRICE):
        raise ValidationError(
            f"Price must be between {MIN_PRICE} and {MAX_PRICE}, got {price_dec}"
        )

    # Quantize to 2 decimals (tick size)
    normalized = price_dec.quantize(PRICE_DECIMALS, rounding=ROUND_HALF_UP)

    return normalized


def validate_size(size: Any, min_size: Optional[Decimal] = None) -> Decimal:
    """
    Validate and normalize order size.

    Args:
        size: Order size in USDC (float, int, str, or Decimal)
        min_size: Minimum allowed size (default: MIN_SIZE)

    Returns:
        Normalized size (Decimal)

    Raises:
        ValidationError: If size is invalid
    """
    if min_size is None:
        min_size = MIN_SIZE
    elif not isinstance(min_size, Decimal):
        min_size = Decimal(str(min_size))

    # Convert to Decimal
    try:
        if isinstance(size, Decimal):
            size_dec = size
        elif isinstance(size, str):
            size_dec = Decimal(size)
        elif isinstance(size, (int, float)):
            size_dec = Decimal(str(size))
        else:
            raise ValidationError(f"Size must be numeric, got {type(size)}")
    except (ValueError, InvalidOperation) as e:
        raise ValidationError(f"Invalid size format: {size}") from e

    # Minimum check
    if size_dec < min_size:
        raise ValidationError(f"Size must be >= {min_size}, got {size_dec}")

    # Quantize to 2 decimals
    normalized = size_dec.quantize(SIZE_DECIMALS, rounding=ROUND_HALF_UP)

    # Check after rounding
    if normalized < min_size:
        raise ValidationError(
            f"Size after rounding ({normalized}) is below minimum {min_size}"
        )

    return normalized


def validate_token_id(token_id: str) -> str:
    """
    Validate token ID format.

    Args:
        token_id: ERC1155 token ID

    Returns:
        Token ID

    Raises:
        ValidationError: If token ID is invalid
    """
    if not isinstance(token_id, str):
        raise ValidationError(f"Token ID must be string, got {type(token_id)}")

    if not token_id:
        raise ValidationError("Token ID cannot be empty")

    # Token IDs are large integers as strings
    if not token_id.isdigit():
        raise ValidationError(f"Token ID must be numeric string, got {token_id}")

    return token_id


def validate_condition_id(condition_id: str) -> str:
    """
    Validate condition ID format (hex string).

    Args:
        condition_id: Market condition ID

    Returns:
        Normalized condition ID

    Raises:
        ValidationError: If condition ID is invalid
    """
    if not isinstance(condition_id, str):
        raise ValidationError(f"Condition ID must be string, got {type(condition_id)}")

    # Remove 0x prefix if present
    if condition_id.startswith("0x"):
        condition_id = condition_id[2:]

    # Validate hex format
    if not re.match(r"^[0-9a-fA-F]+$", condition_id):
        raise ValidationError(f"Condition ID must be hex string, got {condition_id}")

    # Add 0x prefix
    return f"0x{condition_id.lower()}"


def validate_address(address: str) -> str:
    """
    Validate Ethereum address.

    Args:
        address: Ethereum address

    Returns:
        Checksummed address

    Raises:
        ValidationError: If address is invalid
    """
    if not isinstance(address, str):
        raise ValidationError(f"Address must be string, got {type(address)}")

    # Remove 0x prefix if present
    addr = address[2:] if address.startswith("0x") else address

    # Validate hex format and length (20 bytes = 40 hex chars)
    if not re.match(r"^[0-9a-fA-F]{40}$", addr):
        raise ValidationError(f"Invalid Ethereum address: {address}")

    # Return checksummed address using web3
    try:
        from web3 import Web3
        w3 = Web3()
        return w3.to_checksum_address(f"0x{addr}")
    except Exception:
        # Fallback to lowercase if web3 not available
        return f"0x{addr.lower()}"


def validate_private_key(private_key: str) -> str:
    """
    Validate private key format.

    Args:
        private_key: Private key hex string

    Returns:
        Normalized private key

    Raises:
        ValidationError: If private key is invalid
    """
    if not isinstance(private_key, str):
        raise ValidationError(f"Private key must be string, got {type(private_key)}")

    # Remove 0x prefix if present
    key = private_key[2:] if private_key.startswith("0x") else private_key

    # Validate hex format and length (32 bytes = 64 hex chars)
    if not re.match(r"^[0-9a-fA-F]{64}$", key):
        raise ValidationError("Invalid private key format")

    return f"0x{key.lower()}"


def validate_gtd_expiration(
    expiration: int,
    min_offset_seconds: int = 60
) -> int:
    """
    Validate GTD (Good-Til-Date) order expiration timestamp.

    Polymarket requires GTD orders to have expiration at least 60 seconds
    in the future for security.

    Args:
        expiration: Unix timestamp for order expiration
        min_offset_seconds: Minimum seconds into future (default 60)

    Returns:
        Validated expiration timestamp

    Raises:
        OrderExpiredError: If expiration is too soon or in the past
        ValidationError: If expiration format is invalid
    """
    if not isinstance(expiration, int):
        raise ValidationError(f"Expiration must be int, got {type(expiration)}")

    current_time = int(time.time())
    min_expiration = current_time + min_offset_seconds

    if expiration < current_time:
        raise OrderExpiredError(
            f"Expiration {expiration} is in the past (current: {current_time})",
            expiration=expiration
        )

    if expiration < min_expiration:
        raise OrderExpiredError(
            f"GTD expiration must be at least {min_offset_seconds}s in future. "
            f"Got {expiration}, need >= {min_expiration}",
            expiration=expiration
        )

    return expiration


def validate_order(
    token_id: str,
    price: Any,
    size: Any,
    side: str,
    min_size: Optional[Decimal] = None
) -> tuple[str, Decimal, Decimal, str]:
    """
    Validate complete order parameters.

    Args:
        token_id: Token ID
        price: Order price (any numeric type)
        size: Order size (any numeric type)
        side: BUY or SELL
        min_size: Minimum order size (default: MIN_SIZE)

    Returns:
        Tuple of validated (token_id, price, size, side)
        price and size are returned as Decimal

    Raises:
        ValidationError: If any parameter is invalid
    """
    # Validate each component
    validated_token = validate_token_id(token_id)
    validated_price = validate_price(price)
    validated_size = validate_size(size, min_size)

    # Validate side
    if side not in ("BUY", "SELL"):
        raise ValidationError(f"Side must be BUY or SELL, got {side}")

    return validated_token, validated_price, validated_size, side
