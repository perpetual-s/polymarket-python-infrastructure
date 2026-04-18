"""
Order builder with EIP-712 signing.

Implements order construction and signing for Polymarket CLOB.
Uses py_order_utils for EIP-712 signing (official Polymarket library).
Adapted from py-clob-client (MIT License).
"""

import time
import hashlib
from typing import Optional, Dict, Any
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import logging

from ..models import OrderRequest, Side, OrderType
from ..exceptions import ValidationError, TradingError
from ..utils.validators import validate_order
from ..utils.cache import MarketMetadataCache

logger = logging.getLogger(__name__)


# Order constants (from py-clob-client)
BUY = 0
SELL = 1

# Exchange addresses (Polymarket production - Polygon mainnet)
EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"  # Standard exchange
NEG_RISK_EXCHANGE_ADDRESS = "0xC5d563A36AE78145C45a50134d48A1215220f80a"  # Negative risk exchange
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
        idempotency_key: Optional[str] = None,
        signature_type: int = 0,
        funder: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Build and sign order using py_order_utils.

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
            Signed order dict ready for submission (via .dict() method)

        Raises:
            ValidationError: If order parameters invalid
            TradingError: If signing fails
        """
        try:
            from py_order_utils.builders import OrderBuilder as UtilsOrderBuilder
            from py_order_utils.signer import Signer as UtilsSigner
            from py_order_utils.model import OrderData, BUY as UtilsBuy, SELL as UtilsSell

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

            # CRITICAL: Explicit zero-price validation before division
            if order.price == 0 or order.price <= 0:
                raise ValidationError(
                    f"Price must be positive, got {order.price}. "
                    f"Valid range: [{tick_size}, {1 - tick_size}]"
                )

            # Calculate amounts (using Decimal for precision)
            # CRITICAL: Per official py-clob-client, size = number of tokens (NOT USD)
            side_utils = UtilsBuy if order.side == Side.BUY else UtilsSell

            if side_utils == UtilsBuy:
                # BUY: size = tokens to buy
                # taker_amount = tokens to receive (what we get)
                # maker_amount = USDC to pay (what we give)
                taker_amount = self._to_amount(order.size, neg_risk)

                # Round taker amount first (BUY: taker=2 decimals)
                # Tokens have 6 decimals, 2 decimal precision = divisible by 10,000
                taker_amount = self._round_to_precision(taker_amount, 10000)

                # Recalculate maker amount from rounded taker amount for consistency
                # maker_amount = taker_amount × price (in USDC atomic units)
                taker_amount_decimal = Decimal(str(taker_amount)) / Decimal("1e6")  # Convert to decimal tokens
                maker_amount_decimal = taker_amount_decimal * order.price  # Calculate USDC value
                maker_amount = self._to_wei(maker_amount_decimal)  # Convert to USDC atomic units

                # Round maker amount (BUY: maker=4 decimals)
                # USDC has 6 decimals, 4 decimal precision = divisible by 100
                maker_amount = self._round_to_precision(maker_amount, 100)
            else:
                # SELL: size = tokens to sell
                # maker_amount = tokens to give (what we sell)
                # taker_amount = USDC to receive (what we get)
                maker_amount = self._to_amount(order.size, neg_risk)

                # Round maker amount first (SELL: maker=2 decimals)
                # Tokens have 6 decimals, 2 decimal precision = divisible by 10,000
                maker_amount = self._round_to_precision(maker_amount, 10000)

                # Recalculate taker amount from rounded maker amount for consistency
                # taker_amount = maker_amount × price (in USDC atomic units)
                maker_amount_decimal = Decimal(str(maker_amount)) / Decimal("1e6")  # Convert to decimal tokens
                taker_amount_decimal = maker_amount_decimal * order.price  # Calculate USDC value
                taker_amount = self._to_wei(taker_amount_decimal)  # Convert to USDC atomic units

                # Round taker amount (SELL: taker=4 decimals)
                # USDC has 6 decimals, 4 decimal precision = divisible by 100
                taker_amount = self._round_to_precision(taker_amount, 100)

            # Calculate expiration based on order type
            # GTC (Good Till Canceled) → expiration = 0
            # GTD (Good Till Date) → expiration = Unix timestamp
            if order.order_type == OrderType.GTD:
                # GTD orders use provided expiration or default to 30 days
                expiration = order.expiration if order.expiration else int(time.time()) + (30 * 24 * 60 * 60)
            else:
                # GTC, FOK, FAK orders must have expiration = 0
                expiration = 0

            # Build OrderData structure (per py_order_utils)
            # For PROXY wallets: maker = proxy address, signer = EOA address
            # For EOA wallets: maker = signer = EOA address
            maker_address = funder if funder else address

            order_data = OrderData(
                maker=maker_address,
                taker="0x0000000000000000000000000000000000000000",
                tokenId=str(order.token_id),
                makerAmount=str(int(maker_amount)),
                takerAmount=str(int(taker_amount)),
                side=side_utils,
                feeRateBps=str(int(fee_rate_bps)),
                nonce=str(int(nonce)),
                signer=address,
                expiration=str(int(expiration)),
                signatureType=signature_type,
            )

            # Create salt generator (deterministic if idempotency_key provided)
            if idempotency_key:
                # Deterministic salt for retry safety
                salt_value = self.generate_salt_from_key(idempotency_key)
                salt_generator = lambda: salt_value
            else:
                # Random salt (default)
                salt_generator = lambda: self.generate_salt_from_key(None)

            # Select exchange based on neg_risk flag
            # CRITICAL: neg_risk markets use a different exchange contract
            exchange = NEG_RISK_EXCHANGE_ADDRESS if neg_risk else self.exchange

            # Build and sign order using py_order_utils
            builder = UtilsOrderBuilder(
                exchange,
                self.chain_id,
                UtilsSigner(key=private_key),
                salt_generator=salt_generator
            )

            signed_order = builder.build_signed_order(order_data)

            # Convert to dict for API submission
            # Uses SignedOrder.dict() method which handles type transformations
            order_dict = signed_order.dict()

            logger.info(
                f"Built order: {order.side.value} {order.size} @ {order.price} "
                f"(token={order.token_id}, nonce={nonce})"
            )

            return order_dict

        except ImportError as e:
            logger.error(f"py_order_utils not installed: {e}")
            raise TradingError(
                "py_order_utils required for order signing. "
                "Install with: pip install py_order_utils"
            )
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

    def generate_salt_from_key(self, idempotency_key: Optional[str]) -> int:
        """
        Generate deterministic salt from idempotency key.

        If idempotency_key is None, generates random salt (backward compatible).
        If provided, uses SHA-256 hash of key to generate deterministic 32-bit salt.

        CRITICAL: Polymarket API (TypeScript/JavaScript-based) cannot handle integers larger
        than 2^53 - 1 (Number.MAX_SAFE_INTEGER). We use 32-bit salts to ensure compatibility.

        Args:
            idempotency_key: Unique identifier (e.g., database UUID)
                           None for random salt

        Returns:
            32-bit integer salt (0 to 4,294,967,295)

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
            # Random 32-bit salt (JavaScript-safe)
            import secrets
            return secrets.randbits(32)

        # Deterministic: hash the key to get 32-bit salt
        # SHA-256 produces 256 bits, we take first 4 bytes = 32 bits
        hash_bytes = hashlib.sha256(idempotency_key.encode("utf-8")).digest()

        # Convert first 4 bytes to 32-bit integer
        salt = int.from_bytes(hash_bytes[:4], byteorder="big")

        return salt

    def _round_to_precision(self, amount: int, divisor: int) -> int:
        """
        Round amount to nearest multiple of divisor.

        Polymarket requires specific decimal precision for order amounts:
        - BUY: maker (USDC) divisible by 100, taker (tokens) divisible by 10,000
        - SELL: maker (tokens) divisible by 10,000, taker (USDC) divisible by 100

        Args:
            amount: Amount in smallest unit (int)
            divisor: Rounding precision (e.g., 100 for 4 decimals, 10000 for 2 decimals)

        Returns:
            Rounded amount (int)

        Example:
            >>> self._round_to_precision(1020202, 10000)  # Round to nearest 10,000
            1020000  # 1.02 tokens → 1.02 tokens (rounded down)
        """
        # Round to nearest multiple of divisor
        return (amount // divisor) * divisor

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
