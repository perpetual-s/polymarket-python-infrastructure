"""
Order builder with EIP-712 signing.

Implements order construction and signing for Polymarket CLOB.
Adapted from py-clob-client and python-order-utils (MIT License).
"""

import time
import secrets
from typing import Optional, Dict, Any
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import logging

from ..models import OrderRequest, Side, OrderType
from ..exceptions import ValidationError, TradingError
from ..utils.validators import validate_order, validate_price
from ..utils.cache import MarketMetadataCache

logger = logging.getLogger(__name__)


# Order constants (from py-clob-client)
BUY = 0
SELL = 1

# Exchange addresses (Polymarket production)
EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"  # Polygon mainnet
COLLATERAL_TOKEN = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC on Polygon

# Default values
DEFAULT_FEE_RATE_BPS = 0  # 0 basis points = 0%
MIN_TICK_SIZE = Decimal("0.01")
MAX_TICK_SIZE = Decimal("0.99")


class OrderBuilder:
    """
    Builds and signs orders for Polymarket CLOB.

    Handles:
    - Order construction
    - Tick size validation
    - Fee rate resolution
    - Nonce management
    - EIP-712 signing (when web3 available)
    """

    def __init__(
        self,
        chain_id: int = 137,
        exchange: str = EXCHANGE_ADDRESS,
        metadata_cache: Optional[MarketMetadataCache] = None
    ):
        """
        Initialize order builder.

        Args:
            chain_id: Polygon chain ID (137 for mainnet)
            exchange: Exchange contract address
            metadata_cache: Optional metadata cache
        """
        self.chain_id = chain_id
        self.exchange = exchange
        self.metadata_cache = metadata_cache or MarketMetadataCache()

    def build_order(
        self,
        order: OrderRequest,
        private_key: str,
        address: str,
        nonce: int,
        tick_size: Optional[Decimal] = None,
        fee_rate_bps: Optional[int] = None,
        neg_risk: bool = False,
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Build and sign order.

        Args:
            order: Order request
            private_key: Private key for signing
            address: Wallet address
            nonce: Current nonce
            tick_size: Market tick size (fetched if not provided)
            fee_rate_bps: Fee rate in basis points (uses default if not provided)
            neg_risk: Negative risk flag
            idempotency_key: Optional key for deterministic salt generation
                           (prevents duplicate orders on retry)

        Returns:
            Signed order dict ready for submission

        Raises:
            ValidationError: If order parameters invalid
            TradingError: If signing fails
        """
        try:
            # Validate order
            validate_order(
                order.token_id,
                order.price,
                order.size,
                order.side.value
            )

            # Resolve tick size
            if tick_size is None:
                tick_size = self._resolve_tick_size(order.token_id)

            # Validate price against tick size
            if not self._price_valid(order.price, tick_size):
                raise ValidationError(
                    f"Price {order.price} invalid for tick size {tick_size}. "
                    f"Must be between {tick_size} and {1 - tick_size}"
                )

            # Resolve fee rate
            if fee_rate_bps is None:
                fee_rate_bps = self._resolve_fee_rate(order.token_id)

            # Generate salt (unique order ID)
            # If idempotency_key provided, use deterministic salt for retry safety
            salt = self.generate_salt_from_key(idempotency_key)

            # CRITICAL: Explicit zero-price validation before division
            if order.price == 0 or order.price <= 0:
                raise ValidationError(
                    f"Price must be positive, got {order.price}. "
                    f"Valid range: [{tick_size}, {1 - tick_size}]"
                )

            # Calculate amounts (using Decimal for precision)
            side_int = BUY if order.side == Side.BUY else SELL

            if side_int == BUY:
                # Buying: maker amount is USDC, taker amount is tokens
                # size = USD value to spend
                maker_amount = self._to_wei(order.size)  # USDC to pay
                taker_amount = self._to_amount(order.size / order.price, neg_risk)  # Tokens received (division safe)
            else:
                # Selling: maker amount is tokens, taker amount is USDC
                # CRITICAL FIX: size = USD value to sell, so convert to token quantity first
                # Example: size=$10, price=$0.50 → sell 20 tokens for $10
                tokens_to_sell = order.size / order.price  # USD / price = token quantity (division safe)
                maker_amount = self._to_amount(tokens_to_sell, neg_risk)  # Tokens to give
                taker_amount = self._to_wei(order.size)  # USDC to receive (≈ tokens * price)

            # Calculate expiration (default 30 days)
            expiration = int(time.time()) + (30 * 24 * 60 * 60)
            if order.order_type == OrderType.GTD and order.expiration:
                expiration = order.expiration

            # Build order structure
            order_data = {
                "salt": salt,
                "maker": address,
                "signer": address,
                "taker": "0x0000000000000000000000000000000000000000",  # Anyone
                "tokenId": order.token_id,
                "makerAmount": str(maker_amount),
                "takerAmount": str(taker_amount),
                "expiration": str(expiration),
                "nonce": str(nonce),
                "feeRateBps": str(fee_rate_bps),
                "side": str(side_int),
                "signatureType": str(0),  # EOA signature
            }

            # Sign order
            signature = self._sign_order(order_data, private_key)
            order_data["signature"] = signature

            logger.info(
                f"Built order: {order.side.value} {order.size} @ {order.price} "
                f"(token={order.token_id}, nonce={nonce})"
            )

            return order_data

        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Failed to build order: {e}")
            raise TradingError(f"Failed to build order: {e}")

    def _resolve_tick_size(self, token_id: str) -> Decimal:
        """
        Resolve tick size for token.

        Uses cache with fallback to default.

        Args:
            token_id: Token ID

        Returns:
            Tick size (Decimal)
        """
        # Check cache
        cached = self.metadata_cache.get_tick_size(token_id)
        if cached is not None:
            # Ensure it's Decimal
            if isinstance(cached, Decimal):
                return cached
            else:
                return Decimal(str(cached))

        # Use default and cache it
        tick_size = MIN_TICK_SIZE
        self.metadata_cache.set_tick_size(token_id, tick_size)

        logger.debug(f"Using default tick size {tick_size} for token {token_id}")
        return tick_size

    def _resolve_fee_rate(self, token_id: str) -> int:
        """
        Resolve fee rate for token.

        Uses cache with fallback to default.

        Args:
            token_id: Token ID

        Returns:
            Fee rate in basis points
        """
        # Check cache
        cached = self.metadata_cache.get_fee_rate(token_id)
        if cached is not None:
            return cached

        # Use default
        fee_rate = DEFAULT_FEE_RATE_BPS
        self.metadata_cache.set_fee_rate(token_id, fee_rate)

        return fee_rate

    def _price_valid(self, price: Decimal, tick_size: Decimal) -> bool:
        """
        Validate price against tick size.

        Args:
            price: Order price (Decimal)
            tick_size: Market tick size (Decimal)

        Returns:
            True if valid
        """
        # Bounds check
        if price < tick_size or price > (Decimal("1") - tick_size):
            return False

        try:
            # Price should be divisible by tick size
            remainder = price % tick_size
            return remainder == 0 or abs(remainder) < Decimal('0.00001')
        except (ValueError, InvalidOperation) as e:
            # BUG FIX (P1-2): Raise error instead of returning True
            # Invalid prices should be rejected, not allowed
            logger.error(f"Decimal validation failed for price {price}, tick {tick_size}: {e}")
            from ..exceptions import ValidationError
            raise ValidationError(f"Invalid price or tick size: price={price}, tick_size={tick_size}, error={e}")

    def _generate_salt(self) -> int:
        """Generate random salt for order uniqueness."""
        return secrets.randbits(256)

    def generate_salt_from_key(self, idempotency_key: Optional[str]) -> int:
        """
        Generate deterministic salt from idempotency key.

        If idempotency_key is None, generates random salt (backward compatible).
        If provided, uses SHA-256 hash of key to generate deterministic 256-bit salt.

        Args:
            idempotency_key: Unique identifier (e.g., database UUID)
                           None for random salt

        Returns:
            256-bit integer salt

        Example:
            >>> builder = OrderBuilder()
            >>> # Deterministic salt for retry safety
            >>> salt1 = builder.generate_salt_from_key("550e8400-e29b-41d4-a716-446655440000")
            >>> salt2 = builder.generate_salt_from_key("550e8400-e29b-41d4-a716-446655440000")
            >>> assert salt1 == salt2  # Same key → same salt
            >>>
            >>> # Random salt (backward compatible)
            >>> salt3 = builder.generate_salt_from_key(None)
            >>> salt4 = builder.generate_salt_from_key(None)
            >>> assert salt3 != salt4  # Random
        """
        if idempotency_key is None:
            # Backward compatible: random salt
            return self._generate_salt()

        # Deterministic: hash the key to get 256-bit salt
        import hashlib

        # SHA-256 produces 256 bits = 32 bytes
        hash_bytes = hashlib.sha256(idempotency_key.encode("utf-8")).digest()

        # Convert bytes to 256-bit integer
        salt = int.from_bytes(hash_bytes, byteorder="big")

        return salt

    def _to_wei(self, amount: Decimal) -> int:
        """
        Convert USDC amount to wei (6 decimals) with Decimal precision.

        Args:
            amount: Amount in USDC (Decimal)

        Returns:
            Amount in wei (int)
        """
        # Use Decimal arithmetic to avoid float precision loss
        wei = amount * Decimal("1e6")
        # Round to nearest integer (banker's rounding)
        return int(wei.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def _to_amount(self, size: Decimal, neg_risk: bool) -> int:
        """
        Convert size to token amount with Decimal precision.

        Args:
            size: Size in tokens (Decimal)
            neg_risk: Negative risk flag

        Returns:
            Amount in smallest unit (int)
        """
        # Conditional tokens use 6 decimals
        amount = size * Decimal("1e6")
        # Round to nearest integer
        return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def _sign_order(self, order_data: Dict[str, Any], private_key: str) -> str:
        """
        Sign order using EIP-712.

        Args:
            order_data: Order data
            private_key: Private key

        Returns:
            Hex signature

        Raises:
            TradingError: If signing fails
        """
        try:
            from web3 import Web3
            from eth_account.messages import encode_typed_data

            # EIP-712 domain
            domain = {
                "name": "Polymarket CTF Exchange",
                "version": "1",
                "chainId": self.chain_id,
                "verifyingContract": self.exchange
            }

            # Order type
            order_type = [
                {"name": "salt", "type": "uint256"},
                {"name": "maker", "type": "address"},
                {"name": "signer", "type": "address"},
                {"name": "taker", "type": "address"},
                {"name": "tokenId", "type": "uint256"},
                {"name": "makerAmount", "type": "uint256"},
                {"name": "takerAmount", "type": "uint256"},
                {"name": "expiration", "type": "uint256"},
                {"name": "nonce", "type": "uint256"},
                {"name": "feeRateBps", "type": "uint256"},
                {"name": "side", "type": "uint8"},
                {"name": "signatureType", "type": "uint8"},
            ]

            # Convert to int types for signing
            message = {
                "salt": int(order_data["salt"]),
                "maker": order_data["maker"],
                "signer": order_data["signer"],
                "taker": order_data["taker"],
                "tokenId": int(order_data["tokenId"]),
                "makerAmount": int(order_data["makerAmount"]),
                "takerAmount": int(order_data["takerAmount"]),
                "expiration": int(order_data["expiration"]),
                "nonce": int(order_data["nonce"]),
                "feeRateBps": int(order_data["feeRateBps"]),
                "side": int(order_data["side"]),
                "signatureType": int(order_data["signatureType"]),
            }

            # Build typed data
            typed_data = {
                "types": {
                    "EIP712Domain": [
                        {"name": "name", "type": "string"},
                        {"name": "version", "type": "string"},
                        {"name": "chainId", "type": "uint256"},
                        {"name": "verifyingContract", "type": "address"},
                    ],
                    "Order": order_type,
                },
                "primaryType": "Order",
                "domain": domain,
                "message": message,
            }

            # Sign
            w3 = Web3()
            account = w3.eth.account.from_key(private_key)
            encoded = encode_typed_data(full_message=typed_data)
            signature = account.sign_message(encoded)

            return signature.signature.hex()

        except ImportError:
            raise TradingError(
                "web3 and eth-account required for order signing. "
                "Install with: pip install web3 eth-account"
            )
        except Exception as e:
            logger.error(f"Order signing failed: {e}")
            raise TradingError(f"Order signing failed: {e}")

    def set_tick_size(self, token_id: str, tick_size: Decimal) -> None:
        """
        Manually set tick size for token.

        Args:
            token_id: Token ID
            tick_size: Tick size (Decimal)
        """
        self.metadata_cache.set_tick_size(token_id, tick_size)

    def set_fee_rate(self, token_id: str, fee_rate_bps: int) -> None:
        """
        Manually set fee rate for token.

        Args:
            token_id: Token ID
            fee_rate_bps: Fee rate in basis points
        """
        self.metadata_cache.set_fee_rate(token_id, fee_rate_bps)

    def set_neg_risk(self, token_id: str, neg_risk: bool) -> None:
        """
        Manually set negative risk flag for token.

        Args:
            token_id: Token ID
            neg_risk: Negative risk flag
        """
        self.metadata_cache.set_neg_risk(token_id, neg_risk)
